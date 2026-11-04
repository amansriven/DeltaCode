"""Replaces guessed verification commands with checks Delta Code controls."""

import base64
import hashlib
import json
from pathlib import Path

from .models import (
    PatchProposal,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxFile,
    VerificationCommand,
)

HARNESS_DIRECTORY = ".delta-code"
HARNESS_SCRIPT = f"{HARNESS_DIRECTORY}/verify_python.py"
IMPORT_CHECK_ID = "delta-code-imports"
TEST_CHECK_ID = "delta-code-tests"
REGRESSION_EXIT_CODE = 1


def _bundled(path: str, content: bytes) -> SandboxFile:
    return SandboxFile(
        path=path,
        content_base64=base64.b64encode(content).decode(),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def add_python_checks(
    request: SandboxExecutionRequest,
    root: Path,
    proposal: PatchProposal,
    baseline_tests: set[str] | None,
) -> SandboxExecutionRequest:
    """Run the import and regression checks for Python patches instead of the model's commands.

    Its commands were guessed without seeing how the repository runs, and nearly all of them
    failed for unrelated reasons. A patch with no Python edits keeps them.
    """
    edited = {edit.path for edit in proposal.edits}
    if not any(path.endswith(".py") for path in edited):
        return request
    # Read from the repository, since secret-bearing or trimmed files aren't in the bundle.
    originals = {
        path: base64.b64encode((root / path).read_bytes()).decode()
        if (root / path).is_file()
        else None
        for path in sorted(edited)
    }
    files = [
        _bundled(HARNESS_SCRIPT, (Path(__file__).parent / "verify_python.py").read_bytes()),
        _bundled(
            f"{HARNESS_DIRECTORY}/originals.json",
            json.dumps(originals).encode(),
        ),
    ]
    checks = [
        {
            "id": IMPORT_CHECK_ID,
            "kind": "other",
            "argv": ["python", HARNESS_SCRIPT, "imports"],
            "timeout_ms": 120_000,
        }
    ]
    if baseline_tests:
        files.append(
            _bundled(
                f"{HARNESS_DIRECTORY}/baseline-tests.json",
                json.dumps(sorted(baseline_tests)).encode(),
            )
        )
        checks.append(
            {
                "id": TEST_CHECK_ID,
                "kind": "unit_test",
                "argv": ["python", HARNESS_SCRIPT, "tests"],
                "timeout_ms": 120_000,
            }
        )
    return request.model_copy(
        update={
            "files": [*request.files, *files],
            "checks": [VerificationCommand.model_validate(check) for check in checks],
        }
    )


def regression_report(execution: SandboxExecutionResult) -> str | None:
    """What the patch broke, when one of Delta Code's own checks says so."""
    for check in execution.checks:
        if (
            check.id in {IMPORT_CHECK_ID, TEST_CHECK_ID}
            and check.status == "failed"
            and check.exit_code == REGRESSION_EXIT_CODE
        ):
            return check.stdout
    return None
