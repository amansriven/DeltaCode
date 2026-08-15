"""Persistence for digest-keyed workspace AI briefings."""

import hashlib
import json
from datetime import UTC, datetime

from app.db import get_connection
from app.openai_responses import OpenAIUsage


def workspace_snapshot(workspace_id: str) -> tuple[str, dict]:
    with get_connection() as conn:
        migration_rows = conn.execute(
            """SELECT m.data, a.data
               FROM migrations m
               LEFT JOIN migration_attempts a
                 ON a.workspace_id = m.workspace_id AND a.id = m.current_attempt_id
               WHERE m.workspace_id = %s
               ORDER BY m.updated_at DESC, m.id DESC LIMIT 50""",
            (workspace_id,),
        ).fetchall()
        repository_rows = conn.execute(
            """SELECT data FROM repositories WHERE workspace_id = %s AND enabled = TRUE
               ORDER BY updated_at DESC, id DESC LIMIT 100""",
            (workspace_id,),
        ).fetchall()
        provider_rows = conn.execute(
            """SELECT data FROM providers WHERE workspace_id = %s
               ORDER BY updated_at DESC, id DESC LIMIT 50""",
            (workspace_id,),
        ).fetchall()
        source_rows = conn.execute(
            """SELECT data FROM provider_sources WHERE workspace_id = %s
               ORDER BY updated_at DESC, id DESC LIMIT 100""",
            (workspace_id,),
        ).fetchall()
        change_rows = conn.execute(
            """SELECT data FROM change_events WHERE workspace_id = %s
               ORDER BY updated_at DESC, id DESC LIMIT 50""",
            (workspace_id,),
        ).fetchall()
    migrations = []
    for migration_data, attempt_data in migration_rows:
        migration = dict(migration_data)
        migration["attempts"] = [attempt_data] if attempt_data else []
        migrations.append(migration)
    snapshot = {
        "repositories": [row[0] for row in repository_rows],
        "providers": [row[0] for row in provider_rows],
        "sources": [row[0] for row in source_rows],
        "changes": [row[0] for row in change_rows],
        "migrations": migrations,
    }
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return digest, snapshot


def _snapshot_counts(snapshot: dict) -> dict:
    return {
        "migration_count": len(snapshot["migrations"]),
        "repository_count": len(snapshot["repositories"]),
        "provider_count": len(snapshot["providers"]),
        "source_count": len(snapshot["sources"]),
        "change_count": len(snapshot["changes"]),
    }


def get_brief(workspace_id: str) -> dict:
    digest, snapshot = workspace_snapshot(workspace_id)
    counts = _snapshot_counts(snapshot)
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
            **counts,
        }
    status, data, model, input_tokens, cached_tokens, output_tokens, cost, error, updated_at = row
    return {
        "status": status,
        "migration_digest": digest,
        **counts,
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
    digest, snapshot = workspace_snapshot(workspace_id)
    counts = _snapshot_counts(snapshot)
    if not any(counts.values()):
        return {"status": "not_generated", "migration_digest": digest, **counts}
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
            (workspace_id, digest, json.dumps(snapshot), now, now),
        )
    return {"status": "queued", "migration_digest": digest, **counts}


def claim_brief(workspace_id: str, digest: str) -> dict | None:
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
