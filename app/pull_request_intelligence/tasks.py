"""Durable worker for user-triggered pull request overviews."""

from app.hardening.metrics import JobObservation
from app.procrastinate_app import procrastinate_app

from .github import fetch_pull_request_snapshot
from .service import generate_pull_request_overview
from .store import claim_overview, complete_overview, fail_overview


@procrastinate_app.task(name="generate_pull_request_ai_overview")
def generate_pull_request_ai_overview(
    workspace_id: str,
    repository_full_name: str,
    pull_number: int,
) -> None:
    observation = JobObservation("pull_request_intelligence")
    try:
        installation_id = claim_overview(workspace_id, repository_full_name, pull_number)
        if installation_id is None:
            observation.finish("skipped")
            return
        snapshot = fetch_pull_request_snapshot(repository_full_name, pull_number, installation_id)
        overview, model, usage = generate_pull_request_overview(snapshot)
        complete_overview(
            workspace_id,
            repository_full_name,
            pull_number,
            snapshot,
            overview.model_dump(mode="json"),
            model,
            usage,
        )
        observation.finish("completed")
    except Exception as exc:
        observation.finish("failed")
        error_code = getattr(exc, "code", None) or type(exc).__name__.lower()
        fail_overview(workspace_id, repository_full_name, pull_number, str(error_code))
        raise
