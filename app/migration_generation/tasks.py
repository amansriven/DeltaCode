"""Asynchronous Phase 4 migration generation jobs."""

import os
from pathlib import Path

from app.hardening.metrics import JobObservation
from app.ingestion.storage import FilesystemArtifactStore
from app.procrastinate_app import procrastinate_app
from app.repository_intelligence.workspace import (
    GitHubInstallationCredentialBroker,
    GitRepositoryWorkspaceProvider,
)

from .context import assemble_planning_context
from .executor import CloudflareSandboxExecutor
from .intelligence import HttpMigrationIntelligence, OpenAIMigrationIntelligence
from .service import MigrationGenerationService
from .store import claim_attempt, complete_attempt, fail_attempt


def _service() -> MigrationGenerationService:
    sandbox_url = os.environ.get("SANDBOX_EXECUTOR_URL", "")
    sandbox_token = os.environ.get("SANDBOX_EXECUTOR_TOKEN", "")
    execution_enabled = os.environ.get("SANDBOX_EXECUTION_ENABLED", "").lower() == "true"
    if os.environ.get("OPENAI_API_KEY"):
        intelligence = OpenAIMigrationIntelligence()
    else:
        intelligence = HttpMigrationIntelligence(
            os.environ.get("MIGRATION_INTELLIGENCE_URL", ""),
            os.environ.get("MIGRATION_INTELLIGENCE_TOKEN", ""),
        )
    executor = CloudflareSandboxExecutor(
        sandbox_url,
        sandbox_token,
        enabled=execution_enabled,
    )
    artifact_root = Path(os.environ.get("ARTIFACT_STORAGE_ROOT", ".delta-code-artifacts"))
    return MigrationGenerationService(
        intelligence,
        executor,
        FilesystemArtifactStore(artifact_root),
    )


@procrastinate_app.task(name="run_migration_generation")
def run_migration_generation(workspace_id: str, attempt_id: str) -> None:
    observation = JobObservation("generation")
    workspace = None
    service = None
    provider = GitRepositoryWorkspaceProvider(GitHubInstallationCredentialBroker())
    try:
        attempt = claim_attempt(workspace_id, attempt_id)
        if attempt is None:
            observation.finish("skipped")
            return
        credential_handle = f"github-installation:{attempt.repository.installation_id}"
        workspace = provider.materialize(
            attempt.repository,
            attempt.snapshot.commit_sha,
            credential_handle,
        )
        if (
            workspace.commit_sha != attempt.snapshot.commit_sha
            or workspace.content_digest != attempt.snapshot.content_digest
        ):
            raise ValueError("repository_snapshot_mismatch")
        planning_context = assemble_planning_context(
            migration_id=attempt.migration_id,
            attempt_id=attempt.attempt_id,
            previous_attempt_id=attempt.previous_attempt_id,
            developer_instructions=attempt.developer_instructions,
            repository=attempt.repository,
            snapshot=attempt.snapshot,
            change=attempt.change,
            impact=attempt.impact,
            root=Path(workspace.root),
        )
        service = _service()
        result = service.run(planning_context, Path(workspace.root))
        complete_attempt(attempt, result.evidence, result.patch_object_ref)
        observation.finish("completed")
    except Exception as exc:
        observation.finish("failed")
        error_code = getattr(exc, "code", None) or type(exc).__name__.lower()
        fail_attempt(workspace_id, attempt_id, str(error_code))
        raise
    finally:
        try:
            if service is not None:
                close = getattr(getattr(service, "intelligence", None), "close", None)
                if close is not None:
                    close()
        finally:
            if workspace is not None:
                provider.cleanup(workspace)
