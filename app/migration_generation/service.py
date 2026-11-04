"""Provider-neutral plan, patch, sandbox, and review orchestration."""

import difflib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.control_plane.models import (
    ConfidenceBasis,
    EvidenceRepository,
    MigrationEvidence,
    PatchEvidence,
    Recommendation,
    VerificationCheck,
)
from app.hardening.limits import GenerationLimits
from app.ingestion.storage import ArtifactStore

from .context import _redact
from .intelligence import MigrationIntelligence
from .models import (
    ContextFile,
    GenerationProposal,
    PatchProposal,
    PlanningContext,
    SandboxExecutionResult,
)
from .policy import (
    PatchPolicyError,
    ReplacementError,
    build_sandbox_request,
    contains_secret,
    normalize_repository_path,
    validate_patch,
)
from .verification import add_python_checks, regression_report

MAX_REPAIRS = 1
MAX_REPAIR_FEEDBACK_CHARS = 5_000
# Kinds that exercise the migrated code. Formatting and lint say nothing about behavior.
VERIFYING_CHECK_KINDS = {
    "build",
    "type_check",
    "unit_test",
    "generated_test",
    "behavioral_verification",
}
NON_VERIFYING_ARGUMENTS = {"--version", "-V", "--help", "-h"}


@dataclass(frozen=True)
class GenerationResult:
    evidence: MigrationEvidence
    patch_object_ref: str
    attempts: int = 1


def _validate_plan(context: PlanningContext, plan) -> None:
    call_sites = {site.id for site in context.impact.call_sites}
    for step in plan.steps:
        if unknown := sorted(set(step.call_site_ids) - call_sites):
            raise ValueError(f"plan references unknown call sites: {', '.join(unknown)}")
        for path in step.expected_paths:
            normalize_repository_path(path)


def _verification_checks(execution: SandboxExecutionResult) -> list[VerificationCheck]:
    return [
        VerificationCheck(
            id=result.id,
            kind=result.kind,
            status=result.status,
            deterministic=True,
            command=result.command,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            executor=execution.executor,
            summary=(
                "Verification command passed."
                if result.status == "passed"
                else f"Verification command ended with status {result.status}."
            ),
            display_log=(
                result.stdout
                + ("\n" if result.stdout and result.stderr else "")
                + result.stderr
            )[:20_000],
        )
        for result in execution.checks
    ]


def _exercises_the_code(execution: SandboxExecutionResult) -> bool:
    """The model picks its own commands, so approval needs one that could have failed."""
    return any(
        check.status == "passed"
        and check.kind in VERIFYING_CHECK_KINDS
        and not NON_VERIFYING_ARGUMENTS.intersection(check.command.split())
        for check in execution.checks
    )


def _guard_recommendation(
    recommendation: Recommendation,
    execution: SandboxExecutionResult,
) -> Recommendation:
    if recommendation.action != "approve":
        return recommendation
    if execution.status == "passed" and execution.destroyed:
        if _exercises_the_code(execution):
            return recommendation
        return recommendation.model_copy(
            update={
                "action": "revise",
                "rationale": (
                    "Automatic approval is forbidden because no check exercised the migrated code."
                ),
                "confidence": recommendation.confidence.model_copy(
                    update={
                        "basis": ConfidenceBasis.mixed,
                        "unresolved": [
                            *recommendation.confidence.unresolved,
                            "No build, type-check, or test command ran against the patch.",
                        ],
                    }
                ),
            }
        )
    return recommendation.model_copy(
        update={
            "action": "revise" if execution.status == "failed" else "snooze",
            "rationale": (
                "Automatic approval is forbidden because sandbox verification did not pass."
            ),
            "confidence": recommendation.confidence.model_copy(
                update={
                    "basis": ConfidenceBasis.mixed,
                    "unresolved": [
                        *recommendation.confidence.unresolved,
                        f"Sandbox status: {execution.status}; destroyed: {execution.destroyed}",
                    ],
                }
            ),
        }
    )


def _with_repair_feedback(context: PlanningContext, feedback: str) -> PlanningContext:
    """Failure text comes from the repository's code, so it arrives as data like everything else."""
    instructions = (context.developer_instructions or "") + (
        "\n\nA previous attempt at this patch failed. Return every replacement again, applied to "
        "the original files, and fix the cause:\n"
        + _redact(feedback)[0][:MAX_REPAIR_FEEDBACK_CHARS]
    )
    return context.model_copy(update={"developer_instructions": instructions})


def _review_context(context: PlanningContext, patch: PatchProposal) -> PlanningContext:
    """The reviewer needs what changed. Resending untouched files would only double the cost."""
    supplied = {item.path: item for item in context.files}
    files = []
    for edit in patch.edits:
        source = supplied.get(edit.path)
        before = source.restore_omitted(source.content) if source else ""
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            edit.content.splitlines(keepends=True),
            fromfile=edit.path,
            tofile=edit.path,
        )
        files.append(
            ContextFile(
                path=edit.path,
                sha256=edit.expected_sha256 or "0" * 64,
                content="".join(diff),
                start_line=1,
                end_line=1,
            )
        )
    return context.model_copy(update={"files": files})


class MigrationGenerationService:
    def __init__(
        self,
        intelligence: MigrationIntelligence,
        executor,
        artifact_store: ArtifactStore,
        limits: GenerationLimits | None = None,
    ) -> None:
        self.intelligence = intelligence
        self.executor = executor
        self.artifact_store = artifact_store
        self.limits = limits or GenerationLimits.from_env()

    def run(
        self,
        context: PlanningContext,
        root: Path,
        *,
        started_at: datetime | None = None,
        baseline_tests: set[str] | None = None,
    ) -> GenerationResult:
        created_at = started_at or datetime.now(UTC)
        self.limits.validate_context(context)
        attempts = 0
        prompt = context
        while True:
            attempts += 1
            can_repair = attempts <= MAX_REPAIRS
            try:
                proposal = self.intelligence.propose(prompt)
            except ReplacementError as exc:
                if not can_repair:
                    raise
                prompt = _with_repair_feedback(context, str(exc))
                continue
            patch_bytes, patch, execution = self._verify(
                context, root, proposal, baseline_tests
            )
            report = regression_report(execution)
            if report is None or not can_repair:
                break
            prompt = _with_repair_feedback(context, report)
        patch_object_ref = self.artifact_store.put(patch_bytes, patch.sha256)
        review, recommendation = self.intelligence.review(
            _review_context(context, proposal.patch), proposal.plan, patch, execution
        )
        review_payload = json.dumps(
            {
                "review": review.model_dump(mode="json"),
                "recommendation": recommendation.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        if contains_secret(review_payload):
            raise PatchPolicyError("generated review contains credential-like material")
        recommendation = _guard_recommendation(recommendation, execution)
        completed_at = datetime.now(UTC)
        checks = _verification_checks(execution)
        cost = {"sandbox_seconds": execution.duration_ms / 1000}
        model_usage = getattr(self.intelligence, "usage", None)
        if model_usage is not None:
            cost.update(
                {
                    "currency": "USD",
                    "model_input_tokens": model_usage.input_tokens,
                    "model_output_tokens": model_usage.output_tokens,
                    "model_cost": round(model_usage.cost_usd, 8),
                }
            )
        evidence = MigrationEvidence(
            migration_id=context.migration_id,
            attempt_id=context.attempt_id,
            previous_attempt_id=context.previous_attempt_id,
            change_event_id=context.change.id,
            repository=EvidenceRepository(
                id=context.repository.id,
                full_name=context.repository.full_name,
                base_branch=context.snapshot.source_ref,
                base_commit_sha=context.snapshot.commit_sha,
                snapshot_digest=context.snapshot.content_digest,
            ),
            impact=context.impact,
            plan=proposal.plan,
            patch=patch,
            tests=proposal.patch.tests,
            verification_checks=checks,
            review=review,
            recommendation=recommendation,
            tool_versions=[proposal.patch.generator, execution.executor],
            cost=cost,
            created_at=created_at,
            completed_at=completed_at,
        )
        return GenerationResult(
            evidence=evidence, patch_object_ref=patch_object_ref, attempts=attempts
        )

    def _verify(
        self,
        context: PlanningContext,
        root: Path,
        proposal: GenerationProposal,
        baseline_tests: set[str] | None,
    ) -> tuple[bytes, PatchEvidence, SandboxExecutionResult]:
        self.limits.validate_proposal(proposal)
        _validate_plan(context, proposal.plan)
        patch_bytes, patch = validate_patch(root, proposal.plan, proposal.patch)
        request = build_sandbox_request(
            root,
            context.attempt_id,
            context.snapshot.content_digest,
            patch,
            proposal.patch,
        )
        execution = self.executor.execute(
            add_python_checks(request, proposal.patch, baseline_tests)
        )
        self.limits.validate_execution(execution)
        return patch_bytes, patch, execution
