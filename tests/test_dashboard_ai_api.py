from collections.abc import Iterator
from importlib import import_module

import pytest
from fastapi.testclient import TestClient

from app.dashboard_ai import store
from app.main import app
from app.oauth import get_session


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: {
        "github_user_id": 7,
        "github_login": "octocat",
        "accessible_repos": ["acme/api"],
        "repositories": [],
    }
    router = import_module("app.dashboard_ai.router")
    monkeypatch.setattr(
        router,
        "_snapshot",
        lambda _session: ("a" * 64, [{"id": 12, "repository": "acme/api"}]),
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_cached_triage_lookup_never_queues_model_work(client, monkeypatch):
    monkeypatch.setattr(
        store,
        "get_brief",
        lambda user_id, digest, count: {
            "status": "ready",
            "run_digest": digest,
            "run_count": count,
            "brief": {"headline": "Cached"},
        },
    )

    response = client.get("/dashboard/ai-triage")

    assert response.status_code == 200
    assert response.json()["brief"]["headline"] == "Cached"
    assert response.headers["cache-control"] == "private, no-store"


def test_triage_generation_requires_trusted_frontend_origin(client):
    response = client.post(
        "/dashboard/ai-triage",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "untrusted mutation origin"


def test_triage_generation_queues_once_and_returns_accepted(client, monkeypatch):
    monkeypatch.setattr(
        store,
        "queue_brief",
        lambda *_args: (
            {"status": "queued", "run_digest": "a" * 64, "run_count": 1},
            True,
        ),
    )
    queued = []
    task_module = import_module("app.dashboard_ai.tasks")
    monkeypatch.setattr(
        task_module.generate_dashboard_triage,
        "defer",
        lambda **kwargs: queued.append(kwargs),
    )

    response = client.post(
        "/dashboard/ai-triage",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert queued == [{"github_user_id": 7, "run_digest": "a" * 64}]


def test_ready_digest_is_reused_without_another_job(client, monkeypatch):
    monkeypatch.setattr(
        store,
        "queue_brief",
        lambda *_args: (
            {
                "status": "ready",
                "run_digest": "a" * 64,
                "run_count": 1,
                "brief": {"headline": "Already generated"},
            },
            False,
        ),
    )

    response = client.post(
        "/dashboard/ai-triage",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 200
    assert response.json()["brief"]["headline"] == "Already generated"
