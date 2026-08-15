"""Persistence for digest-keyed workspace AI briefings."""

import hashlib
import json
from datetime import UTC, datetime

from app.db import get_connection
from app.openai_responses import OpenAIUsage


def migration_snapshot(workspace_id: str) -> tuple[str, list[dict]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT m.data, a.data
               FROM migrations m
               LEFT JOIN migration_attempts a
                 ON a.workspace_id = m.workspace_id AND a.id = m.current_attempt_id
               WHERE m.workspace_id = %s
               ORDER BY m.updated_at DESC, m.id DESC LIMIT 50""",
            (workspace_id,),
        ).fetchall()
    migrations = []
    for migration_data, attempt_data in rows:
        migration = dict(migration_data)
        migration["attempts"] = [attempt_data] if attempt_data else []
        migrations.append(migration)
    digest = hashlib.sha256(
        json.dumps(migrations, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return digest, migrations


def get_brief(workspace_id: str) -> dict:
    digest, migrations = migration_snapshot(workspace_id)
    with get_connection() as conn:
        row = conn.execute(
            """SELECT status, data, model, input_tokens, cached_input_tokens,
                      output_tokens, cost_usd, error_code, updated_at
               FROM workspace_ai_briefs
               WHERE workspace_id = %s AND migration_digest = %s""",
            (workspace_id, digest),
        ).fetchone()
    if not row:
        return {
            "status": "not_generated",
            "migration_digest": digest,
            "migration_count": len(migrations),
        }
    status, data, model, input_tokens, cached_tokens, output_tokens, cost, error, updated_at = row
    return {
        "status": status,
        "migration_digest": digest,
        "migration_count": len(migrations),
        "brief": data,
        "model": model,
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": float(cost or 0),
        } if model else None,
        "error_code": error,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def queue_brief(workspace_id: str, *, refresh: bool) -> dict:
    digest, migrations = migration_snapshot(workspace_id)
    if not migrations:
        return {"status": "not_generated", "migration_digest": digest, "migration_count": 0}
    now = datetime.now(UTC)
    with get_connection() as conn:
        existing = conn.execute(
            """SELECT status FROM workspace_ai_briefs
               WHERE workspace_id = %s AND migration_digest = %s""",
            (workspace_id, digest),
        ).fetchone()
        if existing and not refresh:
            return get_brief(workspace_id)
        conn.execute(
            """INSERT INTO workspace_ai_briefs
               (workspace_id, migration_digest, status, input_migrations, created_at, updated_at)
               VALUES (%s, %s, 'queued', %s, %s, %s)
               ON CONFLICT (workspace_id, migration_digest) DO UPDATE SET
                 status = 'queued', input_migrations = EXCLUDED.input_migrations,
                 data = NULL, model = NULL, input_tokens = 0, cached_input_tokens = 0,
                 output_tokens = 0, cost_usd = 0, error_code = NULL,
                 updated_at = EXCLUDED.updated_at""",
            (workspace_id, digest, json.dumps(migrations), now, now),
        )
    return {"status": "queued", "migration_digest": digest, "migration_count": len(migrations)}


def claim_brief(workspace_id: str, digest: str) -> list[dict] | None:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT input_migrations FROM workspace_ai_briefs
               WHERE workspace_id = %s AND migration_digest = %s AND status = 'queued'
               FOR UPDATE""",
            (workspace_id, digest),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """UPDATE workspace_ai_briefs SET status = 'running', updated_at = now()
               WHERE workspace_id = %s AND migration_digest = %s""",
            (workspace_id, digest),
        )
    return row[0]


def complete_brief(
    workspace_id: str,
    digest: str,
    data: dict,
    model: str,
    usage: OpenAIUsage,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE workspace_ai_briefs SET status = 'ready', data = %s, model = %s,
               input_tokens = %s, cached_input_tokens = %s, output_tokens = %s,
               cost_usd = %s, error_code = NULL, updated_at = now()
               WHERE workspace_id = %s AND migration_digest = %s AND status = 'running'""",
            (json.dumps(data), model, usage.input_tokens, usage.cached_input_tokens,
             usage.output_tokens, usage.cost_usd, workspace_id, digest),
        )


def fail_brief(workspace_id: str, digest: str, error_code: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE workspace_ai_briefs SET status = 'failed', error_code = %s,
               updated_at = now() WHERE workspace_id = %s AND migration_digest = %s""",
            (error_code[:80], workspace_id, digest),
        )
