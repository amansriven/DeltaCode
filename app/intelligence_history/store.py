"""Read models for chats, repository briefings, and pull-request overviews."""

import base64
import binascii
from datetime import datetime
from typing import Literal

from app.db import get_connection

HistoryKind = Literal["all", "chat", "briefing", "pull_request"]


def _usage(
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    cost_usd,
) -> dict:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": float(cost_usd or 0),
    }


def _cursor(updated_at: datetime, item_id: str) -> str:
    raw = f"{updated_at.isoformat()}|{item_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        timestamp, item_id = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(timestamp), item_id
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid history cursor") from exc


def _chat_items(workspace_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT thread_id, min(created_at), max(updated_at),
                      count(*) FILTER (WHERE role = 'user'),
                      COALESCE(sum(input_tokens), 0),
                      COALESCE(sum(cached_input_tokens), 0),
                      COALESCE(sum(output_tokens), 0),
                      COALESCE(sum(cost_usd), 0),
                      (array_agg(scope ORDER BY updated_at DESC, id DESC))[1],
                      (array_agg(content ORDER BY created_at, id)
                         FILTER (WHERE role = 'user'))[1],
                      (array_agg(COALESCE(data->>'answer', content)
                         ORDER BY created_at DESC, id DESC)
                         FILTER (WHERE role = 'assistant' AND status = 'ready'))[1],
                      (array_agg(status ORDER BY created_at DESC, id DESC)
                         FILTER (WHERE role = 'assistant'))[1],
                      (array_agg(model ORDER BY created_at DESC, id DESC)
                         FILTER (WHERE model IS NOT NULL))[1]
               FROM workspace_ai_chat_messages
               WHERE workspace_id = %s
               GROUP BY thread_id
               ORDER BY max(updated_at) DESC, thread_id DESC LIMIT 300""",
            (workspace_id,),
        ).fetchall()
    items = []
    for row in rows:
        repositories = (row[8] or {}).get("repository_full_names", [])
        first_question = row[9] or "Ask Delta conversation"
        items.append(
            {
                "id": row[0],
                "kind": "chat",
                "title": first_question[:120],
                "summary": (row[10] or "Conversation has no completed answer yet.")[:280],
                "status": row[11] or "ready",
                "repository_full_names": repositories,
                "created_at": row[1].isoformat(),
                "updated_at": row[2].isoformat(),
                "model": row[12],
                "usage": _usage(row[4], row[5], row[6], row[7]),
                "metadata": {"message_count": row[3]},
            }
        )
    return items


def _briefing_items(workspace_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT migration_digest, status, input_migrations, data, model,
                      input_tokens, cached_input_tokens, output_tokens, cost_usd,
                      created_at, updated_at
               FROM workspace_ai_briefs WHERE workspace_id = %s
               ORDER BY updated_at DESC, migration_digest DESC LIMIT 300""",
            (workspace_id,),
        ).fetchall()
    items = []
    for row in rows:
        scope = (row[2] or {}).get("scope", {})
        repositories = scope.get("repository_full_names", [])
        mode = scope.get("mode", "readiness")
        data = row[3] or {}
        title = data.get("headline") or f"{mode.replace('_', ' ').title()} briefing"
        items.append(
            {
                "id": row[0],
                "kind": "briefing",
                "title": title,
                "summary": data.get("executive_summary") or "Briefing generation is not complete.",
                "status": row[1],
                "repository_full_names": repositories,
                "created_at": row[9].isoformat(),
                "updated_at": row[10].isoformat(),
                "model": row[4],
                "usage": _usage(row[5], row[6], row[7], row[8]),
                "metadata": {"mode": mode},
            }
        )
    return items


def _pull_request_items(workspace_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT h.id, h.repository_full_name, h.pull_number, h.status, h.head_sha,
                      h.pull_updated_at, h.input_snapshot, h.data, h.model, h.input_tokens,
                      h.cached_input_tokens, h.output_tokens, h.cost_usd, h.created_at,
                      h.updated_at, (c.current_attempt_id = h.id) AS is_current
               FROM pull_request_ai_overview_attempts h
               LEFT JOIN pull_request_ai_overviews c
                 ON c.workspace_id = h.workspace_id
                AND c.repository_full_name = h.repository_full_name
                AND c.pull_number = h.pull_number
               WHERE h.workspace_id = %s
               ORDER BY h.created_at DESC, h.id DESC LIMIT 300""",
            (workspace_id,),
        ).fetchall()
    items = []
    for row in rows:
        snapshot = row[6] or {}
        overview = row[7] or {}
        items.append(
            {
                "id": row[0],
                "kind": "pull_request",
                "title": (
                    overview.get("headline")
                    or snapshot.get("title")
                    or f"Pull request #{row[2]}"
                ),
                "summary": (
                    overview.get("executive_summary")
                    or "PR overview generation is not complete."
                ),
                "status": row[3],
                "repository_full_names": [row[1]],
                "created_at": row[13].isoformat(),
                "updated_at": row[14].isoformat(),
                "model": row[8],
                "usage": _usage(row[9], row[10], row[11], row[12]),
                "metadata": {
                    "pull_number": row[2],
                    "head_sha": row[4],
                    "pull_updated_at": row[5].isoformat() if row[5] else None,
                    "is_current": row[15],
                },
            }
        )
    return items


def list_history(
    workspace_id: str,
    *,
    kind: HistoryKind,
    repository: str | None,
    query: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict], str | None]:
    position = _decode_cursor(cursor)
    items = []
    if kind in {"all", "chat"}:
        items.extend(_chat_items(workspace_id))
    if kind in {"all", "briefing"}:
        items.extend(_briefing_items(workspace_id))
    if kind in {"all", "pull_request"}:
        items.extend(_pull_request_items(workspace_id))
    if repository:
        items = [item for item in items if repository in item["repository_full_names"]]
    normalized_query = (query or "").strip().lower()
    if normalized_query:
        items = [
            item
            for item in items
            if normalized_query
            in " ".join(
                [item["title"], item["summary"], *item["repository_full_names"]]
            ).lower()
        ]
    items.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    if position:
        items = [
            item
            for item in items
            if (datetime.fromisoformat(item["updated_at"]), item["id"]) < position
        ]
    page = items[:limit]
    next_cursor = None
    if len(items) > limit and page:
        last = page[-1]
        next_cursor = _cursor(datetime.fromisoformat(last["updated_at"]), last["id"])
    return page, next_cursor


def history_detail(workspace_id: str, kind: str, item_id: str) -> dict | None:
    with get_connection() as conn:
        if kind == "chat":
            rows = conn.execute(
                """SELECT id, role, status, content, data, model, input_tokens,
                          cached_input_tokens, output_tokens, cost_usd, error_code, created_at
                   FROM workspace_ai_chat_messages
                   WHERE workspace_id = %s AND thread_id = %s
                   ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END, id""",
                (workspace_id, item_id),
            ).fetchall()
            if not rows:
                return None
            return {
                "kind": kind,
                "id": item_id,
                "messages": [
                    {
                        "id": row[0],
                        "role": row[1],
                        "status": row[2],
                        "content": row[3],
                        "answer": row[4],
                        "model": row[5],
                        "usage": _usage(row[6], row[7], row[8], row[9]),
                        "error_code": row[10],
                        "created_at": row[11].isoformat(),
                    }
                    for row in rows
                ],
            }
        if kind == "briefing":
            row = conn.execute(
                """SELECT status, input_migrations, data, model, input_tokens,
                          cached_input_tokens, output_tokens, cost_usd, created_at, updated_at
                   FROM workspace_ai_briefs
                   WHERE workspace_id = %s AND migration_digest = %s""",
                (workspace_id, item_id),
            ).fetchone()
            if not row:
                return None
            return {
                "kind": kind,
                "id": item_id,
                "status": row[0],
                "scope": (row[1] or {}).get("scope", {}),
                "brief": row[2],
                "model": row[3],
                "usage": _usage(row[4], row[5], row[6], row[7]),
                "created_at": row[8].isoformat(),
                "updated_at": row[9].isoformat(),
            }
        if kind == "pull_request":
            row = conn.execute(
                """SELECT repository_full_name, pull_number, status, head_sha,
                          pull_updated_at, input_snapshot, data, model, input_tokens,
                          cached_input_tokens, output_tokens, cost_usd, created_at, updated_at
                   FROM pull_request_ai_overview_attempts
                   WHERE workspace_id = %s AND id = %s""",
                (workspace_id, item_id),
            ).fetchone()
            if not row:
                return None
            return {
                "kind": kind,
                "id": item_id,
                "status": row[2],
                "repository_full_name": row[0],
                "pull_number": row[1],
                "head_sha": row[3],
                "pull_updated_at": row[4].isoformat() if row[4] else None,
                "snapshot": row[5],
                "overview": row[6],
                "model": row[7],
                "usage": _usage(row[8], row[9], row[10], row[11]),
                "created_at": row[12].isoformat(),
                "updated_at": row[13].isoformat(),
            }
    return None
