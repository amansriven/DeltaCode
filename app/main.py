import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.control_plane import router as control_plane_router
from app.dashboard_ai import router as dashboard_ai_router
from app.db import get_connection, init_schema
from app.github_publishing import router as github_publishing_router
from app.hardening.router import router as hardening_router
from app.ingestion import router as ingestion_router
from app.migration_generation import router as migration_generation_router
from app.oauth import FRONTEND_URL, get_session
from app.oauth import router as oauth_router
from app.procrastinate_app import procrastinate_app
from app.repository_intelligence import router as repository_intelligence_router
from app.tasks import run_comparison
from app.webhook import router as webhook_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_schema()
    procrastinate_app.open()
    try:
        yield
    finally:
        procrastinate_app.close()


app = FastAPI(title="Delta Code", lifespan=lifespan)
app.include_router(webhook_router)
app.include_router(oauth_router)
app.include_router(control_plane_router)
app.include_router(dashboard_ai_router)
app.include_router(ingestion_router)
app.include_router(repository_intelligence_router)
app.include_router(migration_generation_router)
app.include_router(github_publishing_router)
app.include_router(hardening_router)

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
        "api_version": "2026-08-06",
        "capabilities": ["migrations", "providers"],
    }


class CreateRunRequest(BaseModel):
    repo: str
    pr_number: int | None = None


@app.post("/runs")
def create_run(req: CreateRunRequest):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO runs (repo, pr_number) VALUES (%s, %s) RETURNING id",
            (req.repo, req.pr_number),
        ).fetchone()
        run_id = row[0]

    run_comparison.defer(run_id=run_id)
    return {"id": run_id, "status": "pending"}


@app.get("/runs/{run_id}")
def get_run(run_id: int, session: dict = Depends(get_session)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, repo, pr_number, status, result, error, base_ref, base_sha, "
            "head_ref, head_sha, created_at, updated_at FROM runs WHERE id = %s",
            (run_id,),
        ).fetchone()
    if row is None or row[1] not in session["accessible_repos"]:
        raise HTTPException(status_code=404, detail="run not found")
    keys = [
        "id",
        "repo",
        "pr_number",
        "status",
        "result",
        "error",
        "base_ref",
        "base_sha",
        "head_ref",
        "head_sha",
        "created_at",
        "updated_at",
    ]
    return dict(zip(keys, row, strict=True))


@app.get("/runs")
def list_runs(session: dict = Depends(get_session)):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id, repo, pr_number, status, created_at,
                CASE WHEN result IS NOT NULL
                    THEN jsonb_array_length(result->'findings')
                END AS finding_count,
                CASE
                    WHEN result IS NULL THEN NULL
                    WHEN EXISTS (
                        SELECT 1 FROM jsonb_array_elements(result->'findings') f
                        WHERE f->>'kind' = 'regression'
                    ) THEN 'regression'
                    WHEN jsonb_array_length(result->'findings') > 0 THEN 'status_code_changed'
                    ELSE 'none'
                END AS highest_severity
            FROM runs WHERE repo = ANY(%s) ORDER BY id DESC LIMIT 50
            """,
            (session["accessible_repos"],),
        ).fetchall()
    keys = [
        "id",
        "repo",
        "pr_number",
        "status",
        "created_at",
        "finding_count",
        "highest_severity",
    ]
    return [dict(zip(keys, r, strict=True)) for r in rows]


@app.post("/runs/{run_id}/retry")
def retry_run(run_id: int, session: dict = Depends(get_session)):
    with get_connection() as conn:
        row = conn.execute("SELECT repo FROM runs WHERE id = %s", (run_id,)).fetchone()
        if row is None or row[0] not in session["accessible_repos"]:
            raise HTTPException(status_code=404, detail="run not found")
        conn.execute(
            "UPDATE runs SET status = 'pending', result = NULL, error = NULL, updated_at = now() "
            "WHERE id = %s",
            (run_id,),
        )
    run_comparison.defer(run_id=run_id)
    return {"id": run_id, "status": "pending"}
