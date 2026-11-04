import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "app/migration_generation/verify_python.py"


def _run(root: Path, mode: str, originals: dict[str, str | None] | None = None, baseline=None):
    state = root / ".delta-code"
    state.mkdir(exist_ok=True)
    shutil.copy(SCRIPT, state / "verify_python.py")
    encoded = {
        path: base64.b64encode(text.encode()).decode() if text is not None else None
        for path, text in (originals or {}).items()
    }
    (state / "originals.json").write_text(json.dumps(encoded))
    (state / "baseline-tests.json").write_text(json.dumps(baseline or []))
    return subprocess.run(
        [sys.executable, ".delta-code/verify_python.py", mode],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_patch_that_breaks_import_is_a_regression(tmp_path):
    (tmp_path / "app.py").write_text("raise RuntimeError('api_key must be set')\n")

    result = _run(tmp_path, "imports", {"app.py": "value = 1\n"})

    assert result.returncode == 1
    assert "app.py no longer imports: RuntimeError: api_key must be set" in result.stdout


def test_code_that_already_failed_to_import_is_not_blamed_on_the_patch(tmp_path):
    (tmp_path / "app.py").write_text("raise RuntimeError('still broken')\n")

    result = _run(tmp_path, "imports", {"app.py": "raise RuntimeError('broken')\n"})

    assert result.returncode == 0


def test_missing_dependency_is_not_a_regression(tmp_path):
    (tmp_path / "app.py").write_text("import not_installed_anywhere\nvalue = 2\n")

    result = _run(tmp_path, "imports", {"app.py": "import not_installed_anywhere\nvalue = 1\n"})

    assert result.returncode == 0


def test_importing_does_not_see_credentials_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    source = "import os\nvalue = os.environ['OPENAI_API_KEY']\n"
    (tmp_path / "app.py").write_text(source)

    result = _run(tmp_path, "imports", {"app.py": "value = 1\n"})

    assert result.returncode == 1


def test_call_the_installed_package_refuses_is_a_regression(tmp_path):
    (tmp_path / "app.py").write_text(
        "from shutil import copy\n\n\ndef go():\n    return copy('a', 'b', api_base='x')\n"
    )

    result = _run(
        tmp_path,
        "imports",
        {"app.py": "from shutil import copy\n\n\ndef go():\n    return copy('a', 'b')\n"},
    )

    assert result.returncode == 1
    assert "shutil.copy" in result.stdout
    assert "api_base" in result.stdout


def test_bad_call_the_original_already_had_is_not_blamed_on_the_patch(tmp_path):
    source = "from shutil import copy\n\n\ndef go():\n    return copy('a')\n"
    (tmp_path / "app.py").write_text(source + "\nvalue = 2\n")

    result = _run(tmp_path, "imports", {"app.py": source})

    assert result.returncode == 0


def test_valid_and_unresolvable_calls_pass(tmp_path):
    (tmp_path / "app.py").write_text(
        "from shutil import copy\nimport missing_package\n\n\ndef go(client, kwargs):\n"
        "    copy('a', 'b', follow_symlinks=False)\n"
        "    copy(**kwargs)\n"
        "    missing_package.call(1, 2, 3)\n"
        "    return client.anything(nope=1)\n"
    )

    result = _run(tmp_path, "imports", {"app.py": "from shutil import copy\n"})

    assert result.returncode == 0, result.stdout


def test_syntax_error_is_a_regression(tmp_path):
    (tmp_path / "app.py").write_text("def broken(:\n")

    result = _run(tmp_path, "imports", {"app.py": "value = 1\n"})

    assert result.returncode == 1
    assert "does not parse" in result.stdout


def test_patched_files_are_left_in_place_and_new_files_are_handled(tmp_path):
    (tmp_path / "app.py").write_text("value = 2\n")
    (tmp_path / "added.py").write_text("raise ValueError('new')\n")

    result = _run(tmp_path, "imports", {"app.py": "value = 1\n", "added.py": None})

    assert result.returncode == 1
    assert "added.py no longer imports" in result.stdout
    assert (tmp_path / "app.py").read_text() == "value = 2\n"
    assert (tmp_path / "added.py").exists()


def test_a_baseline_test_that_stops_passing_is_a_regression(tmp_path):
    pytest.importorskip("pytest")
    (tmp_path / "test_app.py").write_text(
        "def test_good():\n    assert True\n\n\ndef test_bad():\n    assert False\n"
    )

    result = _run(
        tmp_path, "tests", baseline=["test_app.py::test_good", "test_app.py::test_bad"]
    )

    assert result.returncode == 1
    assert "REGRESSION test_app.py::test_bad" in result.stdout
    assert "REGRESSION test_app.py::test_good" not in result.stdout


def test_a_test_that_already_failed_is_not_blamed_on_the_patch(tmp_path):
    (tmp_path / "test_app.py").write_text(
        "def test_good():\n    assert True\n\n\ndef test_bad():\n    assert False\n"
    )

    result = _run(tmp_path, "tests", baseline=["test_app.py::test_good"])

    assert result.returncode == 0
    assert "All 1 tests" in result.stdout


def test_pytest_usage_failure_gives_no_verdict(tmp_path):
    (tmp_path / "conftest.py").write_text(
        "def pytest_configure(config):\n    raise SystemExit(4)\n"
    )

    result = _run(tmp_path, "tests", baseline=["test_app.py::test_good"])

    assert result.returncode == 3
