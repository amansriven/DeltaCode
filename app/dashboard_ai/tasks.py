from app.procrastinate_app import procrastinate_app

from . import store
from .service import generate_triage_brief


@procrastinate_app.task(name="generate_dashboard_triage")
def generate_dashboard_triage(github_user_id: int, run_digest: str) -> None:
    runs = store.claim_brief(github_user_id, run_digest)
    if runs is None:
        return
    try:
        brief = generate_triage_brief(runs)
        store.complete_brief(
            github_user_id,
            run_digest,
            brief.model_dump(mode="json"),
        )
    except Exception as exc:
        error_code = getattr(exc, "code", None) or type(exc).__name__.lower()
        store.fail_brief(github_user_id, run_digest, error_code)
        raise
