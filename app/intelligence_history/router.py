"""Authenticated intelligence-history API."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.control_plane.store import ensure_workspace
from app.oauth import get_session

from .store import history_detail, list_history

router = APIRouter(prefix="/intelligence/history", tags=["intelligence-history"])


def _workspace(session: Annotated[dict, Depends(get_session)]) -> str:
    return ensure_workspace(session)


@router.get("")
def intelligence_history(
    workspace_id: Annotated[str, Depends(_workspace)],
    kind: Literal["all", "chat", "briefing", "pull_request"] = "all",
    repository: str | None = None,
    query: Annotated[str | None, Query(max_length=160)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
):
    try:
        items, next_cursor = list_history(
            workspace_id,
            kind=kind,
            repository=repository,
            query=query,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{kind}")
def intelligence_history_detail(
    kind: Literal["chat", "briefing", "pull_request"],
    workspace_id: Annotated[str, Depends(_workspace)],
    item_id: Annotated[str, Query(min_length=1, max_length=500)],
):
    result = history_detail(workspace_id, kind, item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="intelligence history item not found")
    return result
