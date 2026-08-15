"""Durable generation task for workspace AI briefings."""

from app.hardening.metrics import JobObservation
from app.procrastinate_app import procrastinate_app

from .service import generate_workspace_brief
from .store import claim_brief, complete_brief, fail_brief


@procrastinate_app.task(name="generate_workspace_ai_brief")
def generate_workspace_ai_brief(workspace_id: str, migration_digest: str) -> None:
    observation = JobObservation("workspace_intelligence")
    try:
        migrations = claim_brief(workspace_id, migration_digest)
        if migrations is None:
            observation.finish("skipped")
            return
        brief, model, usage = generate_workspace_brief(migrations)
        complete_brief(
            workspace_id,
            migration_digest,
            brief.model_dump(mode="json"),
            model,
            usage,
        )
        observation.finish("completed")
    except Exception as exc:
        observation.finish("failed")
        error_code = getattr(exc, "code", None) or type(exc).__name__.lower()
        fail_brief(workspace_id, migration_digest, str(error_code))
        raise
