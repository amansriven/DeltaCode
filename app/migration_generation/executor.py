"""Sandbox executors: the Cloudflare client and a local Docker one for evaluations."""

import base64
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import time
import uuid
from urllib.parse import urlparse

import httpx

from .models import SandboxCheckResult, SandboxExecutionRequest, SandboxExecutionResult
from .policy import SECRET_PATTERNS, PatchPolicyError, normalize_repository_path, validate_argv

MAX_EXECUTOR_RESPONSE_BYTES = 2_000_000
MAX_CHECK_LOG_CHARS = 20_000
WORKSPACE = "/workspace"
REPOSITORY_ROOT = f"{WORKSPACE}/repository"


class SandboxUnavailable(RuntimeError):
    code = "sandbox_unavailable"


class CloudflareSandboxExecutor:
    executor_id = "cloudflare-sandbox"
    executor_version = "0.12.4"

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        enabled: bool,
        client: httpx.Client | None = None,
    ) -> None:
        if not enabled:
            raise SandboxUnavailable("sandbox execution is disabled by policy")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("sandbox executor must use credential-free HTTPS")
        if not bearer_token:
            raise ValueError("sandbox executor token is required")
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.client = client or httpx.Client(timeout=httpx.Timeout(180, connect=5))

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        with self.client.stream(
            "POST",
            f"{self.base_url}/v1/execute",
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            json=request.model_dump(mode="json"),
        ) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_EXECUTOR_RESPONSE_BYTES:
                    raise SandboxUnavailable("sandbox response exceeds limit")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SandboxUnavailable("sandbox returned invalid JSON") from exc
        result = SandboxExecutionResult.model_validate(payload)
        if result.attempt_id != request.attempt_id:
            raise SandboxUnavailable("sandbox response attempt does not match request")
        if result.executor.id != self.executor_id:
            raise SandboxUnavailable("sandbox response executor identity is invalid")
        requested_checks = [(check.id, check.kind) for check in request.checks]
        returned_checks = [(check.id, check.kind) for check in result.checks]
        if returned_checks != requested_checks:
            raise SandboxUnavailable("sandbox response checks do not match request")
        if result.status == "passed" and (
            not result.destroyed or any(check.status != "passed" for check in result.checks)
        ):
            raise SandboxUnavailable("sandbox returned an inconsistent success result")
        return result


class StaticSandboxExecutor:
    def __init__(self, result: SandboxExecutionResult):
        self.result = result

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if request.attempt_id != self.result.attempt_id:
            raise ValueError("sandbox fixture attempt does not match request")
        return self.result.model_copy(deep=True)


class IsolatedContainer:
    """A disposable local container: no network, capabilities, host mounts, or host environment."""

    def __init__(
        self,
        image: str,
        *,
        network: str = "none",
        docker: str = "docker",
        memory: str = "2g",
        cpus: str = "2",
        pids: int = 512,
        workspace_bytes: int = 1_073_741_824,
    ) -> None:
        self.image = image
        self.network = network
        self.docker = docker
        self.memory = memory
        self.cpus = cpus
        self.pids = pids
        self.workspace_bytes = workspace_bytes
        self.name = f"delta-code-sandbox-{uuid.uuid4().hex}"

    def run_arguments(self) -> list[str]:
        arguments = [
            self.docker, "run", "--detach", "--rm", "--name", self.name,
            "--label", "delta-code-sandbox=1",
            "--network", self.network,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids),
            "--memory", self.memory,
            "--memory-swap", self.memory,
            "--cpus", self.cpus,
        ]
        if self.network == "none":
            # Dependency preparation is the only step that gets a network, and it needs root to
            # install; everything that runs repository code is unprivileged and read-only.
            arguments += [
                "--user", "1000:1000",
                "--read-only",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=536870912",
                "--tmpfs",
                f"{WORKSPACE}:rw,nosuid,nodev,uid=1000,gid=1000,size={self.workspace_bytes}",
            ]
        return [*arguments, self.image, "sleep", "infinity"]

    def start(self) -> None:
        subprocess.run(self.run_arguments(), check=True, capture_output=True, timeout=300)

    def upload(self, files: dict[str, bytes]) -> None:
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            for path, content in files.items():
                member = tarfile.TarInfo(path)
                member.size = len(content)
                member.mode = 0o644
                bundle.addfile(member, io.BytesIO(content))
        subprocess.run(
            [self.docker, "exec", self.name, "mkdir", "-p", REPOSITORY_ROOT],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            [self.docker, "exec", "-i", self.name, "tar", "-x", "-C", REPOSITORY_ROOT],
            input=archive.getvalue(),
            check=True,
            capture_output=True,
            timeout=120,
        )

    def exec(
        self,
        argv: list[str],
        *,
        timeout_seconds: float,
        env: dict[str, str] | None = None,
        output_limit: int = MAX_CHECK_LOG_CHARS,
    ) -> tuple[int | None, str, str]:
        """Run argv without a shell. A None exit code means the time limit was exceeded."""
        command = [self.docker, "exec", "--workdir", REPOSITORY_ROOT]
        for key, value in (env or {}).items():
            command += ["--env", f"{key}={value}"]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                completed = subprocess.run(
                    [*command, self.name, *argv],
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                )
                exit_code: int | None = completed.returncode
            except subprocess.TimeoutExpired:
                exit_code = None
            logs = []
            for stream in (stdout, stderr):
                stream.seek(0)
                logs.append(stream.read(output_limit * 4).decode(errors="replace")[:output_limit])
        return exit_code, logs[0], logs[1]

    def destroy(self) -> bool:
        try:
            removed = subprocess.run(
                [self.docker, "rm", "--force", self.name], capture_output=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return removed.returncode == 0 or b"No such container" in removed.stderr


def _redact_log(value: str) -> str:
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value[:MAX_CHECK_LOG_CHARS]


class LocalDockerSandboxExecutor:
    """Evaluation-only executor with the same contract and fail-closed rules as the Worker.

    It runs on the machine that started it, so it is never selected for hosted generation.
    Verification claims from it must say local Docker, not Cloudflare Sandbox.
    """

    executor_id = "local-docker"
    executor_version = "1.0.0"

    def __init__(self, image: str, *, max_command_timeout_ms: int = 120_000, **container) -> None:
        self.image = image
        self.max_command_timeout_ms = max_command_timeout_ms
        self.container = container

    def _write_bundle(self, sandbox: IsolatedContainer, request: SandboxExecutionRequest) -> None:
        files: dict[str, bytes] = {}
        for item in [*request.files, *request.edits]:
            content = base64.b64decode(item.content_base64, validate=True)
            if hashlib.sha256(content).hexdigest() != item.sha256:
                raise ValueError("file_digest_mismatch")
            files[normalize_repository_path(item.path)] = content
        sandbox.upload(files)

    def execute(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        started = time.monotonic()
        sandbox = IsolatedContainer(self.image, **self.container)
        results: list[SandboxCheckResult] = []
        status = "passed"
        try:
            sandbox.start()
            self._write_bundle(sandbox, request)
            for check in request.checks:
                validate_argv(check.argv)
                check_started = time.monotonic()
                timeout_ms = min(check.timeout_ms, self.max_command_timeout_ms)
                exit_code, stdout, stderr = sandbox.exec(
                    check.argv,
                    timeout_seconds=timeout_ms / 1000,
                    env={"CI": "true", "HOME": "/tmp/delta-code-home"},
                )
                timed_out = exit_code is None
                passed = exit_code == 0
                results.append(
                    SandboxCheckResult(
                        id=check.id,
                        kind=check.kind,
                        status="timed_out" if timed_out else "passed" if passed else "failed",
                        command=" ".join(check.argv),
                        exit_code=exit_code,
                        duration_ms=int((time.monotonic() - check_started) * 1000),
                        stdout=_redact_log(stdout),
                        stderr=(
                            "Command exceeded its time limit."
                            if timed_out
                            else _redact_log(stderr)
                        ),
                    )
                )
                if not passed:
                    status = "infrastructure_error" if timed_out else "failed"
                    break
        except (OSError, ValueError, PatchPolicyError, subprocess.SubprocessError):
            status = "infrastructure_error"
        finally:
            destroyed = sandbox.destroy()
        if not destroyed:
            status = "infrastructure_error"
        for check in request.checks[len(results) :]:
            results.append(
                SandboxCheckResult(
                    id=check.id,
                    kind=check.kind,
                    status="blocked",
                    command=" ".join(check.argv),
                    duration_ms=0,
                    stderr="Not run because an earlier sandbox step did not pass.",
                )
            )
        return SandboxExecutionResult(
            attempt_id=request.attempt_id,
            status=status,
            checks=results,
            executor={"id": self.executor_id, "version": self.executor_version},
            duration_ms=int((time.monotonic() - started) * 1000),
            network_policy="deny_all",
            destroyed=destroyed,
        )
