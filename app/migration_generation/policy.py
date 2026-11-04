"""Fail-closed path, patch, command, and secret policy."""

import base64
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from app.control_plane.models import FileChange, MigrationPlan, PatchEvidence
from app.repository_intelligence.workspace import _walk_entries

from .models import PatchProposal, SandboxExecutionRequest, SandboxFile

MAX_BUNDLE_FILES = 1000
MAX_BUNDLE_BYTES = 6_000_000
MAX_EDIT_BYTES = 2_000_000
# An edit replaces the whole file, so a partial answer would silently delete the rest of it.
# Migrations rarely shrink a file, and these limits leave room for removing a compatibility shim.
MAX_LINES_REMOVED_UNCHECKED = 20
MAX_LINE_LOSS_RATIO = 0.15
DENIED_PATH_PREFIXES = (
    ".git",
    ".github/workflows",
    ".circleci",
    ".buildkite",
    ".ssh",
    ".aws",
    ".config/gcloud",
)
DENIED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    "service-account.json",
    ".netrc",
}
# Values that are code rather than credentials: os.getenv("KEY"), config["key"], Optional[str],
# settings.api_key, access_token, SecretStr. Segments allow at most two trailing digits, because
# unquoted secrets usually mix digits in, and a JWT's dotted form is excluded by name.
_CODE_SEGMENT = r"[A-Za-z_]+\d{0,2}"
_CODE_VALUE = (
    r"(?!eyJ)(?:"
    rf"{_CODE_SEGMENT}(?:\.{_CODE_SEGMENT})*\s*[(\[]"
    rf"|{_CODE_SEGMENT}(?:\.{_CODE_SEGMENT})+(?![\w.-])"
    r"|[a-z]+(?:_[a-z]+)+(?![\w.-])"
    r"|[A-Z][a-z]+(?:[A-Z][a-z]+)*(?![\w.-])"
    r")"
)
REDACTED_MARKER = "[REDACTED]"
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"\b(?i:api[_-]?key|secret|token|password)\s*[:=]\s*"
        rf"(?!{_CODE_VALUE})"
        r"(['\"]?)[^'\"\s]{8,}\1"
    ),
)
ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "ruff",
    "mypy",
    "pyright",
    "pip",
    "uv",
    "poetry",
    "node",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    "bun",
    "go",
    "cargo",
    "bundle",
}


class PatchPolicyError(ValueError):
    code = "patch_policy_violation"


def normalize_repository_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise PatchPolicyError("invalid repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PatchPolicyError("repository path escapes the workspace")
    normalized = path.as_posix()
    lowered = normalized.lower()
    if any(
        lowered == prefix or lowered.startswith(f"{prefix}/")
        for prefix in DENIED_PATH_PREFIXES
    ):
        raise PatchPolicyError("repository policy denies this path")
    if path.name.lower() in DENIED_FILE_NAMES or path.name.lower().startswith(".env."):
        raise PatchPolicyError("repository policy denies credential-bearing files")
    if path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}:
        raise PatchPolicyError("repository policy denies key material")
    return normalized


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def validate_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] not in ALLOWED_EXECUTABLES:
        raise PatchPolicyError("verification executable is not allowed")
    for argument in argv:
        if not argument or len(argument) > 500 or any(char in argument for char in "\x00\r\n"):
            raise PatchPolicyError("verification argument is invalid")
    return argv


def validate_patch(
    root: Path,
    plan: MigrationPlan,
    proposal: PatchProposal,
) -> tuple[bytes, PatchEvidence]:
    root = root.resolve()
    if contains_secret(json.dumps(proposal.model_dump(mode="json"), sort_keys=True)):
        raise PatchPolicyError("generated proposal contains credential-like material")
    plan_steps = {step.id: step for step in plan.steps}
    total_bytes = 0
    file_changes = []
    canonical_edits = []
    for edit in sorted(proposal.edits, key=lambda item: item.path):
        path = normalize_repository_path(edit.path)
        if unknown_steps := sorted(set(edit.plan_step_ids) - plan_steps.keys()):
            detail = ", ".join(unknown_steps)
            raise PatchPolicyError(f"edit references unknown plan steps: {detail}")
        expected_paths = {
            expected
            for step_id in edit.plan_step_ids
            for expected in plan_steps[step_id].expected_paths
        }
        if path not in expected_paths:
            raise PatchPolicyError("edit path is not approved by its plan step")
        encoded = edit.content.encode()
        total_bytes += len(encoded)
        if total_bytes > MAX_EDIT_BYTES:
            raise PatchPolicyError("generated patch exceeds byte limit")
        if contains_secret(edit.content):
            raise PatchPolicyError("generated patch contains credential-like material")
        destination = root / path
        if REDACTED_MARKER in edit.content and (
            not destination.is_file() or REDACTED_MARKER.encode() not in destination.read_bytes()
        ):
            raise PatchPolicyError("generated patch writes a redaction marker into the file")
        if destination.is_symlink():
            raise PatchPolicyError("generated patch cannot modify a symbolic link")
        if destination.exists():
            if not destination.is_file() or not destination.resolve().is_relative_to(root):
                raise PatchPolicyError("generated patch target is outside the repository")
            existing = destination.read_bytes()
            expected = hashlib.sha256(existing).hexdigest()
            if edit.expected_sha256 != expected:
                raise PatchPolicyError("generated patch base digest is stale")
            old_lines = len(existing.splitlines())
            lost_lines = old_lines - len(edit.content.splitlines())
            if lost_lines > max(MAX_LINES_REMOVED_UNCHECKED, old_lines * MAX_LINE_LOSS_RATIO):
                raise PatchPolicyError(
                    f"generated patch removes {lost_lines} of {old_lines} lines from a file"
                )
            change_type = "modified"
        else:
            if edit.expected_sha256 is not None:
                raise PatchPolicyError("new file cannot declare an existing-file digest")
            change_type = "added"
        canonical_edits.append(
            {
                "path": path,
                "expected_sha256": edit.expected_sha256,
                "content": edit.content,
                "plan_step_ids": sorted(edit.plan_step_ids),
            }
        )
        file_changes.append(
            FileChange(
                path=path,
                change_type=change_type,
                plan_step_ids=edit.plan_step_ids,
            )
        )
    for command in proposal.verification_commands:
        validate_argv(command.argv)
    artifact_bytes = json.dumps(
        {"schema_version": "1.0", "edits": canonical_edits},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    return artifact_bytes, PatchEvidence(
        artifact_id=f"patch_{digest[:24]}",
        sha256=digest,
        summary=proposal.summary,
        files=file_changes,
    )


def build_sandbox_request(
    root: Path,
    attempt_id: str,
    snapshot_digest: str,
    patch: PatchEvidence,
    proposal: PatchProposal,
) -> SandboxExecutionRequest:
    root = root.resolve()
    files = []
    total_bytes = 0
    for relative, entry_type, value in sorted(_walk_entries(root), key=lambda item: item[0]):
        if entry_type != "file":
            continue
        try:
            path = normalize_repository_path(relative)
        except PatchPolicyError:
            continue
        content = value.read_bytes()
        try:
            if contains_secret(content.decode()):
                continue
        except UnicodeDecodeError:
            pass
        total_bytes += len(content)
        if len(files) >= MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
            raise PatchPolicyError("repository exceeds sandbox upload limits")
        files.append(
            SandboxFile(
                path=path,
                content_base64=base64.b64encode(content).decode(),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    edits = [
        SandboxFile(
            path=normalize_repository_path(edit.path),
            content_base64=base64.b64encode(edit.content.encode()).decode(),
            sha256=hashlib.sha256(edit.content.encode()).hexdigest(),
        )
        for edit in proposal.edits
    ]
    return SandboxExecutionRequest(
        attempt_id=attempt_id,
        snapshot_digest=snapshot_digest,
        patch_digest=patch.sha256,
        files=files,
        edits=edits,
        checks=proposal.verification_commands,
    )
