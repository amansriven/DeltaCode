from importlib import import_module
from types import SimpleNamespace

import pytest

from app.openai_responses import OpenAIUsage
from app.workspace_intelligence import service, tasks
from app.workspace_intelligence.models import GenerateWorkspaceBriefRequest

router = import_module("app.workspace_intelligence.router")


MIGRATIONS = [
    {
        "id": "migration-1",
        "provider_name": "Stripe",
        "repository_full_name": "acme/checkout",
        "change_summary": "Payment source removed",
        "risk": "high",
        "status": "ready",
        "effective_at": "2026-09-01T00:00:00Z",
        "attempts": [],
    }
]


class FakeClient:
    available = True
    model = "gpt-4o"
    usage = OpenAIUsage(input_tokens=120, output_tokens=45, cost_usd=0.00075)

    def generate_json(self, **kwargs):
        assert kwargs["schema_name"] == "delta_code_workspace_brief"
        assert kwargs["max_output_tokens"] == 2400
        return {
            "headline": "One migration needs review",
            "executive_summary": "The Stripe migration is ready for a developer decision.",
            "attention_summary": "1 decision · 0 blocked",
            "priorities": [
                {
                    "migration_id": "migration-1",
                    "title": "Review Stripe migration",
                    "urgency": "high",
                    "recommended_action": "review",
                    "reason": "The migration is ready and has an upcoming deadline.",
                    "evidence": ["High risk", "Ready status"],
                }
            ],
            "portfolio_risks": [],
            "next_actions": [
                {
                    "label": "Review evidence",
                    "detail": "Open the migration and make a decision.",
                    "migration_id": "migration-1",
                }
            ],
        }


def test_workspace_brief_is_structured_and_reports_usage():
    brief, model, usage = service.generate_workspace_brief(MIGRATIONS, FakeClient())

    assert brief.priorities[0].migration_id == "migration-1"
    assert model == "gpt-4o"
    assert usage.input_tokens == 120


def test_workspace_brief_rejects_unknown_migration_references():
    client = FakeClient()
    original = client.generate_json

    def unknown(**kwargs):
        result = original(**kwargs)
        result["priorities"][0]["migration_id"] = "invented"
        return result

    client.generate_json = unknown
    with pytest.raises(ValueError, match="unknown migration"):
        service.generate_workspace_brief(MIGRATIONS, client)


def test_generate_route_queues_durable_task(monkeypatch):
    queued = []
    monkeypatch.setattr(router, "FRONTEND_URL", "https://delta.example")
    monkeypatch.setattr(router, "_configured", lambda: True)
    monkeypatch.setattr(
        router.store,
        "queue_brief",
        lambda *_args, **_kwargs: {
            "status": "queued",
            "migration_digest": "a" * 64,
            "migration_count": 1,
        },
    )
    monkeypatch.setattr(
        router.generate_workspace_ai_brief,
        "defer",
        lambda **kwargs: queued.append(kwargs),
    )
    request = SimpleNamespace(headers={"origin": "https://delta.example"})

    response = router.generate_brief(
        GenerateWorkspaceBriefRequest(refresh=True), request, "workspace-1"
    )

    assert response["status"] == "queued"
    assert queued == [{"workspace_id": "workspace-1", "migration_digest": "a" * 64}]


def test_task_persists_model_usage(monkeypatch):
    completed = []
    monkeypatch.setattr(tasks, "claim_brief", lambda *_args: MIGRATIONS)
    monkeypatch.setattr(
        tasks,
        "generate_workspace_brief",
        lambda _items: (
            service.generate_workspace_brief(MIGRATIONS, FakeClient())[0],
            "gpt-4o",
            OpenAIUsage(input_tokens=12, output_tokens=4, cost_usd=0.00007),
        ),
    )
    monkeypatch.setattr(tasks, "complete_brief", lambda *args: completed.append(args))

    tasks.generate_workspace_ai_brief.func("workspace-1", "b" * 64)

    assert completed[0][0:2] == ("workspace-1", "b" * 64)
    assert completed[0][3] == "gpt-4o"
