"""Durable generation task for workspace AI briefings."""

from app.hardening.metrics import JobObservation
from app.procrastinate_app import procrastinate_app

from .chat_service import generate_dashboard_answer
from .chat_store import claim_message, complete_message, fail_message
from .repository_context import build_repository_context, repository_access_report
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


@procrastinate_app.task(name="generate_dashboard_chat_answer")
def generate_dashboard_chat_answer(workspace_id: str, message_id: str) -> None:
    observation = JobObservation("workspace_chat")
    try:
        payload = claim_message(workspace_id, message_id)
        if payload is None:
            observation.finish("skipped")
            return
        repository_refs = payload.pop("repository_refs", [])
        payload["repository_context"] = build_repository_context(
            repository_refs, payload.get("question", "")
        )
        answer, model, usage = generate_dashboard_answer(payload)
        answer_payload = answer.model_dump(mode="json")
        answer_payload["repository_access"] = repository_access_report(
            payload["repository_context"]
        )
        complete_message(workspace_id, message_id, answer_payload, model, usage)
        observation.finish("completed")
    except Exception as exc:
        observation.finish("failed")
        error_code = getattr(exc, "code", None) or type(exc).__name__.lower()
        fail_message(workspace_id, message_id, str(error_code))
        raise
