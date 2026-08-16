"""Authenticated APIs for recent pull requests and explicit AI overviews."""

import os
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.control_plane.store import ensure_workspace
from app.oauth import FRONTEND_URL, get_session

from .github import GitHubReadError, list_recent_pull_requests
from .models import GeneratePullRequestOverviewRequest
from .store import (
    get_overview,
    get_overview_attempt,
    list_overview_attempts,
    overview_statuses,
    queue_overview,
    repository_installation,
)
from .tasks import generate_pull_request_ai_overview

router = APIRouter(prefix="/pull-requests", tags=["pull-request-intelligence"])


def _context(session: Annotated[dict, Depends(get_session)]) -> tuple[str, dict]:
    return ensure_workspace(session), session


def _configured() -> bool:
    return (
        bool(os.environ.get("OPENAI_API_KEY"))
        or os.environ.get("WORKSPACE_INTELLIGENCE_ENABLED", "").lower() == "true"
    )


def _trusted_origin(request: Request) -> None:
    origin = urlparse(request.headers.get("origin") or "")
    expected = urlparse(FRONTEND_URL)
    if (origin.scheme, origin.netloc) != (expected.scheme, expected.netloc):
        raise HTTPException(status_code=403, detail="untrusted mutation origin")


def _repository_name(owner: str, repository: str) -> str:
    return f"{owner}/{repository}"


@router.get("")
def recent_pull_requests(
    context: Annotated[tuple[str, dict], Depends(_context)],
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
):
    workspace_id, session = context
    try:
        items = list_recent_pull_requests(session.get("repositories", []), limit=limit)
    except GitHubReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    statuses = overview_statuses(workspace_id, items)
    for item in items:
        item["ai_overview"] = statuses.get(
            (item["repository_full_name"], item["number"]),
            {"status": "not_generated"},
        )
    return {"items": items, "configured": _configured()}


@router.get("/{owner}/{repository}/{pull_number}/overview")
def pull_request_overview(
    owner: str,
    repository: str,
    pull_number: int,
    context: Annotated[tuple[str, dict], Depends(_context)],
):
    workspace_id, session = context
    full_name = _repository_name(owner, repository)
    if repository_installation(session, full_name) is None:
        raise HTTPException(status_code=404, detail="pull request repository is unavailable")
    return {**get_overview(workspace_id, full_name, pull_number), "configured": _configured()}


@router.get("/{owner}/{repository}/{pull_number}/overview/history")
def pull_request_overview_history(
    owner: str,
    repository: str,
    pull_number: int,
    context: Annotated[tuple[str, dict], Depends(_context)],
):
    workspace_id, session = context
    full_name = _repository_name(owner, repository)
    if repository_installation(session, full_name) is None:
        raise HTTPException(status_code=404, detail="pull request repository is unavailable")
    return {
        "items": list_overview_attempts(workspace_id, full_name, pull_number),
        "configured": _configured(),
    }


@router.get("/{owner}/{repository}/{pull_number}/overview/history/{attempt_id}")
def pull_request_overview_attempt(
    owner: str,
    repository: str,
    pull_number: int,
    attempt_id: str,
    context: Annotated[tuple[str, dict], Depends(_context)],
):
    workspace_id, session = context
    full_name = _repository_name(owner, repository)
    if repository_installation(session, full_name) is None:
        raise HTTPException(status_code=404, detail="pull request repository is unavailable")
    attempt = get_overview_attempt(
        workspace_id, full_name, pull_number, attempt_id
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="pull request review attempt not found")
    return {**attempt, "configured": _configured()}


@router.post(
    "/{owner}/{repository}/{pull_number}/overview",
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_overview(
    owner: str,
    repository: str,
    pull_number: int,
    body: GeneratePullRequestOverviewRequest,
    request: Request,
    context: Annotated[tuple[str, dict], Depends(_context)],
):
    _trusted_origin(request)
    if not _configured():
        raise HTTPException(status_code=503, detail="OpenAI is not configured")
    workspace_id, session = context
    full_name = _repository_name(owner, repository)
    installation_id = repository_installation(session, full_name)
    if installation_id is None:
        raise HTTPException(status_code=404, detail="pull request repository is unavailable")
    response = queue_overview(
        workspace_id, full_name, pull_number, installation_id, refresh=body.refresh
    )
    enqueue_task = response.pop("_enqueue_task", False)
    if enqueue_task:
        generate_pull_request_ai_overview.defer(
            workspace_id=workspace_id,
            repository_full_name=full_name,
            pull_number=pull_number,
            attempt_id=response["attempt_id"],
        )
    return {**response, "configured": True}
