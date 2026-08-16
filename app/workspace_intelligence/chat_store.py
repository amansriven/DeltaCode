"""Durable chat history and bounded dashboard context."""

import json
import secrets
import uuid

from app.db import get_connection
from app.openai_responses import OpenAIUsage

from .store import workspace_snapshot


def create_exchange(
    workspace_id: str,
    thread_id: str | None,
    message: str,
    repositories: list[str],
) -> dict:
    resolved_thread = thread_id or secrets.token_urlsafe(18)
    user_id = str(uuid.uuid4())
    assistant_id = str(uuid.uuid4())
    scope = {"repository_full_names": repositories}
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO workspace_ai_chat_messages
               (id, workspace_id, thread_id, role, status, content, scope)
               VALUES (%s, %s, %s, 'user', 'ready', %s, %s),
                      (%s, %s, %s, 'assistant', 'queued', NULL, %s)""",
            (
                user_id,
                workspace_id,
                resolved_thread,
                message,
                json.dumps(scope),
                assistant_id,
                workspace_id,
                resolved_thread,
                json.dumps(scope),
            ),
        )
    return {"thread_id": resolved_thread, "message_id": assistant_id, "status": "queued"}


def list_messages(workspace_id: str, thread_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, role, status, content, data, model, input_tokens,
                      cached_input_tokens, output_tokens, cost_usd, error_code, created_at
               FROM workspace_ai_chat_messages
               WHERE workspace_id = %s AND thread_id = %s
               ORDER BY created_at,
                        CASE role WHEN 'user' THEN 0 ELSE 1 END,
                        id LIMIT 40""",
            (workspace_id, thread_id),
        ).fetchall()
    return [
        {
            "id": row[0],
            "role": row[1],
            "status": row[2],
            "content": row[3],
            "answer": row[4],
            "model": row[5],
            "usage": {
                "input_tokens": row[6],
                "cached_input_tokens": row[7],
                "output_tokens": row[8],
                "estimated_cost_usd": float(row[9] or 0),
            }
            if row[5]
            else None,
            "error_code": row[10],
            "created_at": row[11].isoformat(),
        }
        for row in rows
    ]


def claim_message(workspace_id: str, message_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """UPDATE workspace_ai_chat_messages SET status = 'running', updated_at = now()
               WHERE workspace_id = %s AND id = %s AND role = 'assistant'
                 AND status = 'queued' RETURNING thread_id, scope""",
            (workspace_id, message_id),
        ).fetchone()
        if not row:
            return None
        history_rows = conn.execute(
            """SELECT role, content, data FROM workspace_ai_chat_messages
               WHERE workspace_id = %s AND thread_id = %s AND status = 'ready'
               ORDER BY created_at DESC,
                        CASE role WHEN 'assistant' THEN 0 ELSE 1 END,
                        id DESC LIMIT 10""",
            (workspace_id, row[0]),
        ).fetchall()
        pr_rows = conn.execute(
            """SELECT repository_full_name, pull_number, data, updated_at
               FROM pull_request_ai_overviews
               WHERE workspace_id = %s AND status = 'ready'
                 AND repository_full_name = ANY(%s)
               ORDER BY updated_at DESC LIMIT 10""",
            (workspace_id, row[1].get("repository_full_names", [])),
        ).fetchall()
        repository_rows = conn.execute(
            """SELECT full_name, default_branch, installation_id
               FROM repositories WHERE workspace_id = %s AND enabled = TRUE
                 AND full_name = ANY(%s)""",
            (workspace_id, row[1].get("repository_full_names", [])),
        ).fetchall()
    repositories = row[1].get("repository_full_names", [])
    _digest, snapshot = workspace_snapshot(workspace_id, repositories, "repository_health")
    snapshot["pull_request_overviews"] = [
        {
            "repository_full_name": item[0],
            "pull_number": item[1],
            "overview": item[2],
            "updated_at": item[3].isoformat(),
        }
        for item in pr_rows
    ]
    history = []
    for role, content, data in reversed(history_rows):
        answer = data.get("answer") if isinstance(data, dict) else None
        history.append({"role": role, "content": content or answer or ""})
    question = next(
        (item["content"] for item in reversed(history) if item["role"] == "user"),
        "",
    )
    return {
        "thread_id": row[0],
        "scope": row[1],
        "history": history,
        "question": question,
        "repository_refs": [
            {
                "full_name": item[0],
                "default_branch": item[1],
                "installation_id": item[2],
            }
            for item in repository_rows
            if item[2] is not None
        ],
        "dashboard": snapshot,
    }


def complete_message(
    workspace_id: str,
    message_id: str,
    answer: dict,
    model: str,
    usage: OpenAIUsage,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE workspace_ai_chat_messages SET status = 'ready', content = %s,
               data = %s, model = %s, input_tokens = %s, cached_input_tokens = %s,
               output_tokens = %s, cost_usd = %s, error_code = NULL, updated_at = now()
               WHERE workspace_id = %s AND id = %s AND status = 'running'""",
            (
                answer["answer"],
                json.dumps(answer),
                model,
                usage.input_tokens,
                usage.cached_input_tokens,
                usage.output_tokens,
                usage.cost_usd,
                workspace_id,
                message_id,
            ),
        )


def fail_message(workspace_id: str, message_id: str, error_code: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE workspace_ai_chat_messages SET status = 'failed', error_code = %s,
               updated_at = now() WHERE workspace_id = %s AND id = %s""",
            (error_code[:80], workspace_id, message_id),
        )
