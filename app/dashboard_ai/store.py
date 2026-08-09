import hashlib
import json
from datetime import datetime
from typing import Any

from app.db import get_connection

MAX_TRIAGE_RUNS = 20
MAX_FINDINGS_PER_RUN = 3


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _compact_finding(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    request = value.get("request") if isinstance(value.get("request"), dict) else {}
    base = value.get("base_response") if isinstance(value.get("base_response"), dict) else {}
    head = value.get("pr_response") if isinstance(value.get("pr_response"), dict) else {}
    return {
        "case": _text(value.get("case"), 160),
        "kind": _text(value.get("kind"), 40),
        "method": _text(request.get("method"), 12),
        "path": _text(request.get("path"), 500),
        "base_status": base.get("status_code"),
        "head_status": head.get("status_code"),
    }


def _compact_run(row: tuple) -> dict:
    result = row[9] if isinstance(row[9], dict) else {}
    findings = result.get("findings") if isinstance(result.get("findings"), list) else []
    compact_findings = [
        finding
        for item in findings[:MAX_FINDINGS_PER_RUN]
        if (finding := _compact_finding(item)) is not None
    ]
    return {
        "id": row[0],
        "repository": _text(row[1], 200),
        "pull_request": row[2],
        "status": _text(row[3], 30),
        "created_at": row[4].isoformat() if isinstance(row[4], datetime) else str(row[4]),
        "updated_at": row[5].isoformat() if isinstance(row[5], datetime) else str(row[5]),
        "finding_count": row[6],
        "highest_severity": row[7],
        "error_code": _text(row[8], 80) if row[8] else None,
        "findings": compact_findings,
    }


def current_snapshot(accessible_repos: list[str]) -> tuple[str, list[dict]]:
    if not accessible_repos:
        runs: list[dict] = []
    else:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, repo, pr_number, status, created_at, updated_at,
                    CASE WHEN result IS NOT NULL
                        THEN jsonb_array_length(result->'findings')
                    END AS finding_count,
                    CASE
                        WHEN result IS NULL THEN NULL
                        WHEN EXISTS (
                            SELECT 1 FROM jsonb_array_elements(result->'findings') f
                            WHERE f->>'kind' = 'regression'
                        ) THEN 'regression'
                        WHEN jsonb_array_length(result->'findings') > 0
                            THEN 'status_code_changed'
                        ELSE 'none'
                    END AS highest_severity,
                    CASE WHEN error IS NULL THEN NULL
                        ELSE split_part(error, ':', 1)
                    END AS error_code,
                    result
                FROM runs
                WHERE repo = ANY(%s)
                ORDER BY id DESC
                LIMIT %s
                """,
                (accessible_repos, MAX_TRIAGE_RUNS),
            ).fetchall()
        runs = [_compact_run(row) for row in rows]

    serialized = json.dumps(runs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest(), runs


def _response(record: tuple | None, *, run_digest: str, run_count: int) -> dict:
    if record is None:
        return {
            "status": "not_generated",
            "run_digest": run_digest,
            "run_count": run_count,
        }
    status, data, error_code, updated_at = record
    response = {
        "status": status,
        "run_digest": run_digest,
        "run_count": run_count,
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
    }
    if status == "ready" and isinstance(data, dict):
        response["brief"] = data
    if status == "failed":
        response["error_code"] = error_code or "generation_failed"
    return response


def get_brief(github_user_id: int, run_digest: str, run_count: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT status, data, error_code, updated_at
               FROM dashboard_ai_briefs
               WHERE github_user_id = %s AND run_digest = %s""",
            (github_user_id, run_digest),
        ).fetchone()
    return _response(row, run_digest=run_digest, run_count=run_count)


def queue_brief(
    github_user_id: int, run_digest: str, runs: list[dict]
) -> tuple[dict, bool]:
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO dashboard_ai_briefs
                   (github_user_id, run_digest, status, input_runs)
               VALUES (%s, %s, 'queued', %s)
               ON CONFLICT (github_user_id, run_digest) DO NOTHING
               RETURNING status, data, error_code, updated_at""",
            (github_user_id, run_digest, json.dumps(runs)),
        ).fetchone()
        if row is not None:
            should_defer = True
        else:
            row = conn.execute(
                """SELECT status, data, error_code, updated_at
                   FROM dashboard_ai_briefs
                   WHERE github_user_id = %s AND run_digest = %s
                   FOR UPDATE""",
                (github_user_id, run_digest),
            ).fetchone()
            should_defer = False
            if row[0] == "failed":
                row = conn.execute(
                    """UPDATE dashboard_ai_briefs
                       SET status = 'queued', input_runs = %s, data = NULL,
                           error_code = NULL, updated_at = now()
                       WHERE github_user_id = %s AND run_digest = %s
                       RETURNING status, data, error_code, updated_at""",
                    (json.dumps(runs), github_user_id, run_digest),
                ).fetchone()
                should_defer = True
    return _response(row, run_digest=run_digest, run_count=len(runs)), should_defer


def claim_brief(github_user_id: int, run_digest: str) -> list[dict] | None:
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE dashboard_ai_briefs
               SET status = 'running', updated_at = now()
               WHERE github_user_id = %s AND run_digest = %s AND status = 'queued'
               RETURNING input_runs""",
            (github_user_id, run_digest),
        ).fetchone()
    return row[0] if row else None


def complete_brief(github_user_id: int, run_digest: str, data: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE dashboard_ai_briefs
               SET status = 'ready', data = %s, error_code = NULL, updated_at = now()
               WHERE github_user_id = %s AND run_digest = %s AND status = 'running'""",
            (json.dumps(data), github_user_id, run_digest),
        )


def fail_brief(github_user_id: int, run_digest: str, error_code: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE dashboard_ai_briefs
               SET status = 'failed', error_code = %s, updated_at = now()
               WHERE github_user_id = %s AND run_digest = %s
                 AND status IN ('queued', 'running')""",
            (error_code[:80], github_user_id, run_digest),
        )
