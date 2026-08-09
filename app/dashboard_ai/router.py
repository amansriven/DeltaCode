from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.oauth import FRONTEND_URL, get_session

from . import store

router = APIRouter(prefix="/dashboard", tags=["dashboard-ai"])


def _require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    trusted = urlparse(FRONTEND_URL)
    candidate = urlparse(origin) if origin else None
    if not candidate or (candidate.scheme, candidate.netloc) != (trusted.scheme, trusted.netloc):
        raise HTTPException(status_code=403, detail="untrusted mutation origin")


def _snapshot(session: dict) -> tuple[str, list[dict]]:
    return store.current_snapshot(session.get("accessible_repos", []))


@router.get("/ai-triage")
def get_ai_triage(
    response: Response,
    session: Annotated[dict, Depends(get_session)],
):
    response.headers["Cache-Control"] = "private, no-store"
    run_digest, runs = _snapshot(session)
    return store.get_brief(session["github_user_id"], run_digest, len(runs))


@router.post("/ai-triage")
def create_ai_triage(
    request: Request,
    response: Response,
    session: Annotated[dict, Depends(get_session)],
):
    _require_trusted_origin(request)
    response.headers["Cache-Control"] = "private, no-store"
    run_digest, runs = _snapshot(session)
    if not runs:
        raise HTTPException(status_code=409, detail="no verification runs are available")

    result, should_defer = store.queue_brief(session["github_user_id"], run_digest, runs)
    if should_defer:
        try:
            from .tasks import generate_dashboard_triage

            generate_dashboard_triage.defer(
                github_user_id=session["github_user_id"],
                run_digest=run_digest,
            )
        except Exception as exc:
            store.fail_brief(session["github_user_id"], run_digest, "queue_unavailable")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI triage queue is unavailable",
            ) from exc

    if result["status"] in {"queued", "running"}:
        response.status_code = status.HTTP_202_ACCEPTED
    return result
