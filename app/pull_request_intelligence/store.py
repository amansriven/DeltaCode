"""Persistence and workspace authorization for PR intelligence."""

import json
from datetime import UTC, datetime

from app.db import get_connection
from app.openai_responses import OpenAIUsage


def repository_installation(session: dict, repository_full_name: str) -> int | None:
    for repository in session.get("repositories", []):
        if repository.get("full_name") != repository_full_name:
            continue
        installation_id = repository.get("installation_id")
        return int(installation_id) if installation_id is not None else None
    return None


def overview_statuses(workspace_id: str, items: list[dict]) -> dict[tuple[str, int], dict]:
    if not items:
        return {}
    pairs = {(item["repository_full_name"], item["number"]) for item in items}
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT repository_full_name, pull_number, status, head_sha, model,
                      input_tokens, cached_input_tokens, output_tokens, cost_usd,
                      error_code, updated_at
               FROM pull_request_ai_overviews
               WHERE workspace_id = %s""",
            (workspace_id,),
        ).fetchall()
    return {
        (row[0], row[1]): {
            "status": row[2],
            "head_sha": row[3],
            "model": row[4],
            "usage": {
                "input_tokens": row[5],
                "cached_input_tokens": row[6],
                "output_tokens": row[7],
                "estimated_cost_usd": float(row[8] or 0),
            }
            if row[4]
            else None,
            "error_code": row[9],
            "updated_at": row[10].isoformat() if row[10] else None,
        }
        for row in rows
        if (row[0], row[1]) in pairs
    }


def get_overview(workspace_id: str, repository: str, pull_number: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT status, head_sha, pull_updated_at, data, model, input_tokens,
                      cached_input_tokens, output_tokens, cost_usd, error_code, updated_at
               FROM pull_request_ai_overviews
               WHERE workspace_id = %s AND repository_full_name = %s AND pull_number = %s""",
            (workspace_id, repository, pull_number),
        ).fetchone()
    if not row:
        return {
            "status": "not_generated",
            "repository_full_name": repository,
            "pull_number": pull_number,
        }
    return {
        "status": row[0],
        "repository_full_name": repository,
        "pull_number": pull_number,
        "head_sha": row[1],
        "pull_updated_at": row[2].isoformat() if row[2] else None,
        "overview": row[3],
        "model": row[4],
        "usage": {
            "input_tokens": row[5],
            "cached_input_tokens": row[6],
            "output_tokens": row[7],
            "estimated_cost_usd": float(row[8] or 0),
        }
        if row[4]
        else None,
        "error_code": row[9],
        "updated_at": row[10].isoformat() if row[10] else None,
    }


def queue_overview(
    workspace_id: str,
    repository: str,
    pull_number: int,
    installation_id: int,
    *,
    refresh: bool,
) -> dict:
    with get_connection() as conn:
        existing = conn.execute(
            """SELECT status FROM pull_request_ai_overviews
               WHERE workspace_id = %s AND repository_full_name = %s AND pull_number = %s""",
            (workspace_id, repository, pull_number),
        ).fetchone()
        if existing and not refresh:
            return get_overview(workspace_id, repository, pull_number)
        now = datetime.now(UTC)
        conn.execute(
            """INSERT INTO pull_request_ai_overviews
               (workspace_id, repository_full_name, pull_number, installation_id,
                status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'queued', %s, %s)
               ON CONFLICT (workspace_id, repository_full_name, pull_number) DO UPDATE SET
                 installation_id = EXCLUDED.installation_id, status = 'queued',
                 head_sha = NULL, pull_updated_at = NULL, input_snapshot = NULL,
                 data = NULL, model = NULL, input_tokens = 0, cached_input_tokens = 0,
                 output_tokens = 0, cost_usd = 0, error_code = NULL,
                 updated_at = EXCLUDED.updated_at""",
            (workspace_id, repository, pull_number, installation_id, now, now),
        )
    return {"status": "queued", "repository_full_name": repository, "pull_number": pull_number}


def claim_overview(workspace_id: str, repository: str, pull_number: int) -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE pull_request_ai_overviews SET status = 'running', updated_at = now()
               WHERE workspace_id = %s AND repository_full_name = %s AND pull_number = %s
                 AND status = 'queued' RETURNING installation_id""",
            (workspace_id, repository, pull_number),
        ).fetchone()
    return int(row[0]) if row else None


def complete_overview(
    workspace_id: str,
    repository: str,
    pull_number: int,
    snapshot: dict,
    overview: dict,
    model: str,
    usage: OpenAIUsage,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE pull_request_ai_overviews SET status = 'ready', head_sha = %s,
               pull_updated_at = %s, input_snapshot = %s, data = %s, model = %s,
               input_tokens = %s, cached_input_tokens = %s, output_tokens = %s,
               cost_usd = %s, error_code = NULL, updated_at = now()
               WHERE workspace_id = %s AND repository_full_name = %s AND pull_number = %s
                 AND status = 'running'""",
            (
                (snapshot.get("head") or {}).get("sha"),
                snapshot.get("updated_at"),
                json.dumps(snapshot),
                json.dumps(overview),
                model,
                usage.input_tokens,
                usage.cached_input_tokens,
                usage.output_tokens,
                usage.cost_usd,
                workspace_id,
                repository,
                pull_number,
            ),
        )


def fail_overview(
    workspace_id: str,
    repository: str,
    pull_number: int,
    error_code: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE pull_request_ai_overviews SET status = 'failed', error_code = %s,
               updated_at = now() WHERE workspace_id = %s AND repository_full_name = %s
               AND pull_number = %s""",
            (error_code[:80], workspace_id, repository, pull_number),
        )
