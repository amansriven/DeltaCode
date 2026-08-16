from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest

from app.intelligence_history import store

router = import_module("app.intelligence_history.router")


def _item(item_id: str, kind: str, updated_at: datetime, repository: str) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "title": f"History {item_id}",
        "summary": f"Summary for {repository}",
        "status": "ready",
        "repository_full_names": [repository],
        "created_at": (updated_at - timedelta(minutes=1)).isoformat(),
        "updated_at": updated_at.isoformat(),
        "model": "gpt-4o",
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 5,
            "estimated_cost_usd": 0.001,
        },
        "metadata": {},
    }


def test_history_combines_filters_and_paginates(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        store,
        "_chat_items",
        lambda _workspace: [_item("chat-1", "chat", now, "acme/api")],
    )
    monkeypatch.setattr(
        store,
        "_briefing_items",
        lambda _workspace: [
            _item("brief-1", "briefing", now - timedelta(minutes=1), "acme/api")
        ],
    )
    monkeypatch.setattr(
        store,
        "_pull_request_items",
        lambda _workspace: [
            _item("acme/web#7", "pull_request", now - timedelta(minutes=2), "acme/web")
        ],
    )

    first, cursor = store.list_history(
        "workspace-1",
        kind="all",
        repository=None,
        query=None,
        cursor=None,
        limit=2,
    )
    second, next_cursor = store.list_history(
        "workspace-1",
        kind="all",
        repository=None,
        query=None,
        cursor=cursor,
        limit=2,
    )

    assert [item["id"] for item in first] == ["chat-1", "brief-1"]
    assert [item["id"] for item in second] == ["acme/web#7"]
    assert next_cursor is None

    repository_items, _ = store.list_history(
        "workspace-1",
        kind="all",
        repository="acme/web",
        query="web",
        cursor=None,
        limit=10,
    )
    assert [item["id"] for item in repository_items] == ["acme/web#7"]


def test_history_rejects_invalid_cursor(monkeypatch):
    monkeypatch.setattr(store, "_chat_items", lambda _workspace: [])
    monkeypatch.setattr(store, "_briefing_items", lambda _workspace: [])
    monkeypatch.setattr(store, "_pull_request_items", lambda _workspace: [])

    with pytest.raises(ValueError, match="invalid history cursor"):
        store.list_history(
            "workspace-1",
            kind="all",
            repository=None,
            query=None,
            cursor="not-a-cursor",
            limit=10,
        )


def test_history_route_is_workspace_scoped(monkeypatch):
    captured = []
    monkeypatch.setattr(
        router,
        "list_history",
        lambda workspace_id, **kwargs: (captured.append((workspace_id, kwargs)) or [], None),
    )

    response = router.intelligence_history(
        workspace_id="workspace-1",
        kind="chat",
        repository="acme/api",
        query="oauth",
        cursor=None,
        limit=20,
    )

    assert response == {"items": [], "next_cursor": None}
    assert captured[0][0] == "workspace-1"
    assert captured[0][1]["repository"] == "acme/api"


def test_history_detail_returns_not_found(monkeypatch):
    monkeypatch.setattr(router, "history_detail", lambda *_args: None)

    with pytest.raises(Exception) as exc_info:
        router.intelligence_history_detail(
            kind="briefing",
            workspace_id="workspace-1",
            item_id="missing",
        )

    assert getattr(exc_info.value, "status_code", None) == 404
