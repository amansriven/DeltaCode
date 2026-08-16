"""Authenticated API for migration-native workspace intelligence."""

import os
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.control_plane import store as control_plane_store
from app.oauth import FRONTEND_URL, get_session

from . import store
from .chat_models import DashboardChatRequest
from .chat_store import create_exchange, list_messages
from .models import GenerateWorkspaceBriefRequest
from .tasks import generate_dashboard_chat_answer, generate_workspace_ai_brief

router = APIRouter(prefix="/intelligence", tags=["workspace-intelligence"])


def _workspace_context(
    session: Annotated[dict, Depends(get_session)],
) -> tuple[str, dict]:
    return control_plane_store.ensure_workspace(session), session


def _configured() -> bool:
    return (
        bool(os.environ.get("OPENAI_API_KEY"))
        or os.environ.get("WORKSPACE_INTELLIGENCE_ENABLED", "").lower() == "true"
    )


def _require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    trusted = urlparse(FRONTEND_URL)
    candidate = urlparse(origin) if origin else None
    if not candidate or (candidate.scheme, candidate.netloc) != (trusted.scheme, trusted.netloc):
        raise HTTPException(status_code=403, detail="untrusted mutation origin")


def _authorized_repositories(session: dict, requested: list[str]) -> list[str]:
    accessible = set(session.get("accessible_repos", []))
    selected = list(dict.fromkeys(requested))
    if not selected or len(selected) > 20 or not set(selected).issubset(accessible):
        raise HTTPException(status_code=422, detail="select 1 to 20 accessible repositories")
    return selected


@router.get("/briefing")
def get_workspace_brief(
    context: Annotated[tuple[str, dict], Depends(_workspace_context)],
    repository: Annotated[list[str] | None, Query()] = None,
    mode: str = "readiness",
):
    workspace_id, session = context
    selected = _authorized_repositories(session, repository or [])
    if mode not in {"readiness", "repository_health", "migration_portfolio"}:
        raise HTTPException(status_code=422, detail="unsupported briefing mode")
    return {
        **store.get_brief(workspace_id, selected, mode),
        "configured": _configured(),
    }


@router.post("/briefing", status_code=status.HTTP_202_ACCEPTED)
def generate_brief(
    body: GenerateWorkspaceBriefRequest,
    request: Request,
    context: Annotated[tuple[str, dict], Depends(_workspace_context)],
):
    _require_trusted_origin(request)
    if not _configured():
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
    workspace_id, session = context
    selected = _authorized_repositories(session, body.repository_full_names)
    response = store.queue_brief(workspace_id, selected, body.mode, refresh=body.refresh)
    if response["status"] == "queued":
        generate_workspace_ai_brief.defer(
            workspace_id=workspace_id,
            migration_digest=response["migration_digest"],
        )
    return {**response, "configured": True}


@router.get("/chat/{thread_id}")
def chat_history(
    thread_id: str,
    context: Annotated[tuple[str, dict], Depends(_workspace_context)],
):
    if not 8 <= len(thread_id) <= 100:
        raise HTTPException(status_code=422, detail="invalid chat thread")
    return {
        "thread_id": thread_id,
        "messages": list_messages(context[0], thread_id),
        "configured": _configured(),
    }


@router.post("/chat", status_code=status.HTTP_202_ACCEPTED)
def ask_dashboard(
    body: DashboardChatRequest,
    request: Request,
    context: Annotated[tuple[str, dict], Depends(_workspace_context)],
):
    _require_trusted_origin(request)
    if not _configured():
        raise HTTPException(status_code=503, detail="OpenAI is not configured")
    workspace_id, session = context
    selected = _authorized_repositories(session, body.repository_full_names)
    response = create_exchange(workspace_id, body.thread_id, body.message.strip(), selected)
    generate_dashboard_chat_answer.defer(
        workspace_id=workspace_id, message_id=response["message_id"]
    )
    return {**response, "configured": True}
