"""Bounded, redacted context assembly for migration intelligence."""

import hashlib
import json
from pathlib import Path
from typing import Any

from app.control_plane.models import ImpactEvidence, NormalizedChange
from app.repository_intelligence.models import RepositoryRef, RepositorySnapshot

from .models import ContextFile, PlanningContext
from .policy import SECRET_PATTERNS, PatchPolicyError, normalize_repository_path

MAX_CONTEXT_FILES = 20
MAX_CONTEXT_BYTES = 70_000
MAX_CONTEXT_FILE_BYTES = 64_000
MAX_CHANGE_VALUE_BYTES = 20_000


def _redact(value: str) -> tuple[str, int]:
    redacted = value
    count = 0
    for pattern in SECRET_PATTERNS:
        redacted, replacements = pattern.subn("[REDACTED]", redacted)
        count += replacements
    return redacted, count


def _bounded_value(value: Any) -> Any:
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    if len(encoded) <= MAX_CHANGE_VALUE_BYTES:
        return value
    return {"truncated": True, "sha256": hashlib.sha256(encoded).hexdigest()}


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)[0]
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            sensitive_key = any(
                marker in str(key).lower()
                for marker in ("api_key", "apikey", "secret", "token", "password")
            )
            redacted[key] = (
                "[REDACTED]"
                if sensitive_key and isinstance(item, str) and len(item) >= 8
                else _redact_value(item)
            )
        return redacted
    return value


def assemble_planning_context(
    *,
    migration_id: str,
    attempt_id: str,
    previous_attempt_id: str | None = None,
    developer_instructions: str | None = None,
    repository: RepositoryRef,
    snapshot: RepositorySnapshot,
    change: NormalizedChange,
    impact: ImpactEvidence,
    root: Path,
) -> PlanningContext:
    if impact.conclusion != "affected":
        raise ValueError("automatic generation requires an affected assessment")
    root = root.resolve()
    evidence_paths = {
        *[site.path for site in impact.call_sites],
        *[
            dependency.source_path
            for dependency in impact.dependency_matches
            if dependency.source_path
        ],
    }
    paths = sorted({normalize_repository_path(path) for path in evidence_paths})
    if len(paths) > MAX_CONTEXT_FILES:
        raise PatchPolicyError("impact exceeds model context file limit")
    files = []
    total_bytes = 0
    for relative in paths:
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise PatchPolicyError("impact call site is not a safe regular file")
        content = path.read_bytes()
        if len(content) > MAX_CONTEXT_FILE_BYTES:
            raise PatchPolicyError("impact call-site file exceeds context limit")
        try:
            text = content.decode()
        except UnicodeDecodeError as exc:
            raise PatchPolicyError("impact call-site file is not UTF-8 text") from exc
        redacted, count = _redact(text)
        total_bytes += len(redacted.encode())
        if total_bytes > MAX_CONTEXT_BYTES:
            raise PatchPolicyError("migration context exceeds byte limit")
        files.append(
            ContextFile(
                path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                content=redacted,
                start_line=1,
                end_line=max(1, len(text.splitlines())),
                redaction_count=count,
            )
        )
    bounded_change = change.model_copy(
        update={
            "summary": _redact(change.summary)[0],
            "before": _bounded_value(_redact_value(change.before)),
            "after": _bounded_value(_redact_value(change.after)),
            "metadata": {},
            "claims": [
                claim.model_copy(
                    update={
                        "summary": _redact(claim.summary)[0],
                        "locator": _redact(claim.locator)[0] if claim.locator else None,
                    }
                )
                for claim in change.claims
            ],
            "migration_guidance": [
                guidance.model_copy(update={"summary": _redact(guidance.summary)[0]})
                for guidance in change.migration_guidance
            ],
            "targets": [target.model_copy(update={"metadata": {}}) for target in change.targets],
        }
    )
    return PlanningContext(
        migration_id=migration_id,
        attempt_id=attempt_id,
        previous_attempt_id=previous_attempt_id,
        developer_instructions=(
            _redact(developer_instructions)[0] if developer_instructions else None
        ),
        repository=repository,
        snapshot=snapshot,
        change=bounded_change,
        impact=impact,
        files=files,
        denied_paths=[*normalize_denied_paths()],
    )


def normalize_denied_paths() -> tuple[str, ...]:
    return (".git/**", ".github/workflows/**", ".env*", "**/*private-key*", "**/*.pem")
