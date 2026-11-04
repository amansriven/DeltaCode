"""Isolation claims are only worth making against a real container, so these use Docker."""

import base64
import hashlib
import shutil
import subprocess

import pytest

from app.migration_generation.executor import IsolatedContainer, LocalDockerSandboxExecutor
from app.migration_generation.models import SandboxExecutionRequest, VerificationCommand

IMAGE = "python:3.12-slim"


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker is not running")


def _file(path: str, content: bytes, *, digest: str | None = None) -> dict:
    return {
        "path": path,
        "content_base64": base64.b64encode(content).decode(),
        "sha256": digest or hashlib.sha256(content).hexdigest(),
    }


def _command(identifier: str, *argv: str, timeout_ms: int = 60_000) -> VerificationCommand:
    return VerificationCommand(
        id=identifier, kind="unit_test", argv=list(argv), timeout_ms=timeout_ms
    )


def _request(*checks, files=None, edits=None) -> SandboxExecutionRequest:
    return SandboxExecutionRequest(
        attempt_id="attempt-1",
        snapshot_digest="sha256:" + "a" * 64,
        patch_digest="b" * 64,
        files=files if files is not None else [_file("pkg/app.py", b"VALUE = 'base'\n")],
        edits=edits if edits is not None else [_file("pkg/app.py", b"VALUE = 'edited'\n")],
        checks=list(checks),
    )


def _run(*checks, **bundle):
    return LocalDockerSandboxExecutor(IMAGE).execute(_request(*checks, **bundle))


def _python(identifier: str, code: str, **options) -> VerificationCommand:
    return _command(identifier, "python", "-c", code, **options)


def test_edits_overlay_the_snapshot_and_a_passing_run_is_destroyed():
    result = _run(
        _python("read", "from pkg.app import VALUE; assert VALUE == 'edited'; print('ok')")
    )

    assert result.status == "passed"
    assert result.destroyed is True
    assert result.network_policy == "deny_all"
    assert result.executor.id == "local-docker"
    assert result.checks[0].stdout.strip() == "ok"


def test_multiline_arguments_are_refused_by_policy():
    result = _run(_python("multiline", "import os\nprint(os.getcwd())"))

    assert result.status == "infrastructure_error"
    assert [item.status for item in result.checks] == ["blocked"]


def test_container_is_removed_after_the_run():
    _run(_python("noop", "pass"))

    listed = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", "label=delta-code-sandbox=1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert listed.stdout.strip() == ""


def test_network_is_unreachable_from_inside():
    code = (
        "import socket\n"
        "socket.setdefaulttimeout(5)\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53))\n"
        "except OSError:\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit('network was reachable')\n"
    )

    result = _run(
        _command("network", "python", "probe.py"),
        files=[_file("probe.py", code.encode())],
    )

    assert result.status == "passed", result.checks[0].stderr


def test_host_environment_is_not_visible(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host-secret-that-must-not-leak")
    code = "import os; assert 'OPENAI_API_KEY' not in os.environ; print(os.getuid())"

    result = _run(_python("env", code))

    assert result.status == "passed"
    assert result.checks[0].stdout.strip() == "1000"


def test_only_the_workspace_and_tmp_are_writable():
    code = (
        "import pathlib\n"
        "pathlib.Path('written.txt').write_text('ok')\n"
        "pathlib.Path('/tmp/written.txt').write_text('ok')\n"
        "try:\n"
        "    pathlib.Path('/etc/written.txt').write_text('no')\n"
        "except OSError:\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit('root filesystem was writable')\n"
    )

    result = _run(
        _command("filesystem", "python", "probe.py"),
        files=[_file("probe.py", code.encode())],
    )

    assert result.status == "passed", result.checks[0].stderr


def test_failed_check_blocks_later_checks_and_fails_the_run():
    result = _run(
        _python("fails", "raise SystemExit(3)"),
        _python("never", "pass"),
    )

    assert result.status == "failed"
    assert [item.status for item in result.checks] == ["failed", "blocked"]
    assert result.checks[0].exit_code == 3
    assert result.destroyed is True


def test_timeout_is_not_reported_as_a_test_failure():
    result = _run(
        _python("hangs", "import time; time.sleep(60)", timeout_ms=1_000),
        _python("never", "pass"),
    )

    assert result.status == "infrastructure_error"
    assert [item.status for item in result.checks] == ["timed_out", "blocked"]
    assert result.destroyed is True


def test_digest_mismatch_fails_closed_without_running_checks():
    bad = _file("pkg/app.py", b"VALUE = 'x'\n", digest="c" * 64)

    result = _run(_python("never", "pass"), edits=[bad])

    assert result.status == "infrastructure_error"
    assert [item.status for item in result.checks] == ["blocked"]
    assert result.destroyed is True


def test_paths_that_escape_the_workspace_fail_closed():
    result = _run(_python("never", "pass"), edits=[_file("../escape.py", b"x = 1\n")])

    assert result.status == "infrastructure_error"
    assert [item.status for item in result.checks] == ["blocked"]


def test_disallowed_executable_is_refused():
    result = _run(_command("shell", "sh", "-c", "echo hi"))

    assert result.status == "infrastructure_error"
    assert [item.status for item in result.checks] == ["blocked"]


def test_secrets_in_logs_are_redacted():
    secret = "sk-" + "a" * 30
    result = _run(_python("leaky", f"print('{secret}')"))

    assert secret not in result.checks[0].stdout
    assert "[REDACTED]" in result.checks[0].stdout


def test_isolation_flags_are_requested_for_every_code_running_container():
    arguments = IsolatedContainer(IMAGE).run_arguments()

    for expected in (
        ["--network", "none"],
        ["--cap-drop", "ALL"],
        ["--security-opt", "no-new-privileges"],
        ["--user", "1000:1000"],
    ):
        position = arguments.index(expected[0])
        assert arguments[position : position + 2] == expected
    assert "--read-only" in arguments
    assert "--volume" not in arguments and "--mount" not in arguments
