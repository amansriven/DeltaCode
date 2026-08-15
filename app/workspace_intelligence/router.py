"""Authenticated API for migration-native workspace intelligence."""

import os
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.control_plane import store as control_plane_store
from app.oauth import FRONTEND_URL, get_session

from . import store
from .models import GenerateWorkspaceBriefRequest
from .tasks import generate_workspace_ai_brief

router = APIRouter(prefix="/intelligence", tags=["workspace-intelligence"])


def _workspace(session: Annotated[dict, Depends(get_session)]) -> str:
    return control_plane_store.ensure_workspace(session)


def _configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) or os.environ.get(
        "WORKSPACE_INTELLIGENCE_ENABLED", ""
    ).lower() == "true"


def _require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    trusted = urlparse(FRONTEND_URL)
    candidate = urlparse(origin) if origin else None
    if not candidate or (candidate.scheme, candidate.netloc) != (trusted.scheme, trusted.netloc):
        raise HTTPException(status_code=403, detail="untrusted mutation origin")


@router.get("/briefing")
def get_workspace_brief(workspace_id: Annotated[str, Depends(_workspace)]):
    return {**store.get_brief(workspace_id), "configured": _configured()}


@router.post("/briefing", status_code=status.HTTP_202_ACCEPTED)
def generate_brief(
    body: GenerateWorkspaceBriefRequest,
    request: Request,
    workspace_id: Annotated[str, Depends(_workspace)],
):
    _require_trusted_origin(request)
    if not _configured():
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
    response = store.queue_brief(workspace_id, refresh=body.refresh)
    if response["status"] == "queued":
        generate_workspace_ai_brief.defer(
            workspace_id=workspace_id,
            migration_digest=response["migration_digest"],
        )
    return {**response, "configured": True}
