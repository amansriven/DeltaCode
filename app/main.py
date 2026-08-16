import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.control_plane import router as control_plane_router
from app.db import init_schema
from app.github_publishing import router as github_publishing_router
from app.hardening.router import router as hardening_router
from app.ingestion import router as ingestion_router
from app.migration_generation import router as migration_generation_router
from app.oauth import FRONTEND_URL
from app.oauth import router as oauth_router
from app.procrastinate_app import procrastinate_app
from app.pull_request_intelligence import router as pull_request_intelligence_router
from app.repository_intelligence import router as repository_intelligence_router
from app.workspace_intelligence import router as workspace_intelligence_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_schema()
    procrastinate_app.open()
    try:
        yield
    finally:
        procrastinate_app.close()


app = FastAPI(title="Delta Code", lifespan=lifespan)
app.include_router(oauth_router)
app.include_router(control_plane_router)
app.include_router(ingestion_router)
app.include_router(repository_intelligence_router)
app.include_router(migration_generation_router)
app.include_router(github_publishing_router)
app.include_router(hardening_router)
app.include_router(workspace_intelligence_router)
app.include_router(pull_request_intelligence_router)

allowed_origins = {
    FRONTEND_URL,
    "http://localhost:3000",
    "http://127.0.0.1:3000",
}
allowed_origins.update(
    origin.strip().rstrip("/")
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str | list[str]]:
    """Return liveness and the capabilities required by the current frontend."""
    return {
        "status": "ok",
        "api_version": "2026-08-15",
        "capabilities": [
            "migrations",
            "providers",
            "workspace_intelligence",
            "dashboard_chat",
            "pull_requests",
        ],
    }
