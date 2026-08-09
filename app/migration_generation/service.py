"""Provider-neutral plan, patch, sandbox, and review orchestration."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.control_plane.models import (
    ConfidenceBasis,
    EvidenceRepository,
    MigrationEvidence,
    Recommendation,
    VerificationCheck,
)
from app.hardening.limits import GenerationLimits
from app.ingestion.storage import ArtifactStore

from .intelligence import MigrationIntelligence
from .models import PlanningContext, SandboxExecutionResult
from .policy import (
    PatchPolicyError,
    build_sandbox_request,
    contains_secret,
    normalize_repository_path,
    validate_patch,
)


@dataclass(frozen=True)
class GenerationResult:
    evidence: MigrationEvidence
    patch_object_ref: str


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


def _guard_recommendation(
    recommendation: Recommendation,
    execution: SandboxExecutionResult,
) -> Recommendation:
    if recommendation.action != "approve" or (
        execution.status == "passed" and execution.destroyed
    ):
        return recommendation
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
    ) -> GenerationResult:
        created_at = started_at or datetime.now(UTC)
        self.limits.validate_context(context)
        proposal = self.intelligence.propose(context)
        self.limits.validate_proposal(proposal)
        _validate_plan(context, proposal.plan)
        patch_bytes, patch = validate_patch(root, proposal.plan, proposal.patch)
        patch_object_ref = self.artifact_store.put(patch_bytes, patch.sha256)
        request = build_sandbox_request(
            root,
            context.attempt_id,
            context.snapshot.content_digest,
            patch,
            proposal.patch,
        )
        execution = self.executor.execute(request)
        self.limits.validate_execution(execution)
        review, recommendation = self.intelligence.review(
            context, proposal.plan, patch, execution
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
        return GenerationResult(evidence=evidence, patch_object_ref=patch_object_ref)
