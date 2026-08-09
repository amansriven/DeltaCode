import pytest

from app.dashboard_ai import store, tasks
from app.dashboard_ai.models import TriageBriefData


def test_dashboard_triage_task_claims_generates_and_completes(monkeypatch):
    runs = [{"id": 12, "repository": "acme/api"}]
    completed = []
    monkeypatch.setattr(store, "claim_brief", lambda *_args: runs)
    monkeypatch.setattr(
        tasks,
        "generate_triage_brief",
        lambda value: TriageBriefData(
            headline="One run needs attention",
            summary="A regression was reproduced.",
            priorities=[],
            watch_items=[],
            model="gpt-4o",
            input_tokens=80,
            output_tokens=20,
            estimated_cost_usd=0.0004,
        ),
    )
    monkeypatch.setattr(
        store,
        "complete_brief",
        lambda user_id, digest, data: completed.append((user_id, digest, data)),
    )

    tasks.generate_dashboard_triage(7, "a" * 64)

    assert completed[0][0:2] == (7, "a" * 64)
    assert completed[0][2]["headline"] == "One run needs attention"
    assert completed[0][2]["model"] == "gpt-4o"


def test_dashboard_triage_task_records_safe_error_code(monkeypatch):
    monkeypatch.setattr(store, "claim_brief", lambda *_args: [{"id": 12}])
    monkeypatch.setattr(
        tasks,
        "generate_triage_brief",
        lambda _runs: (_ for _ in ()).throw(RuntimeError("provider leaked detail")),
    )
    failed = []
    monkeypatch.setattr(
        store,
        "fail_brief",
        lambda user_id, digest, code: failed.append((user_id, digest, code)),
    )

    with pytest.raises(RuntimeError):
        tasks.generate_dashboard_triage(7, "a" * 64)

    assert failed == [(7, "a" * 64, "runtimeerror")]
