from app.dashboard_ai.service import generate_triage_brief
from app.openai_responses import OpenAIUsage


class FakeClient:
    model = "gpt-4o"
    usage = OpenAIUsage(input_tokens=120, output_tokens=40, cost_usd=0.0007)

    def __init__(self):
        self.request = None

    def generate_json(self, **kwargs):
        self.request = kwargs
        return {
            "headline": "One regression needs attention",
            "summary": "The latest verification reproduced a breaking request change.",
            "priorities": [
                {
                    "run_id": 12,
                    "urgency": "high",
                    "title": "Review the checkout regression",
                    "reason": "The same request changed from 201 to 422.",
                },
                {
                    "run_id": 999,
                    "urgency": "low",
                    "title": "Invented run",
                    "reason": "This run was not supplied.",
                },
            ],
            "watch_items": ["A second verification run is still active."],
        }


def test_triage_brief_is_structured_bounded_and_rejects_unknown_run_ids():
    client = FakeClient()
    brief = generate_triage_brief(
        [
            {
                "id": 12,
                "repository": "acme/api",
                "status": "done",
                "finding_count": 1,
                "highest_severity": "regression",
                "findings": [
                    {
                        "case": "omit_discount",
                        "kind": "regression",
                        "method": "POST",
                        "path": "/items",
                        "base_status": 201,
                        "head_status": 422,
                    }
                ],
            }
        ],
        client=client,
    )

    assert [priority.run_id for priority in brief.priorities] == [12]
    assert brief.model == "gpt-4o"
    assert brief.input_tokens == 120
    assert brief.output_tokens == 40
    assert brief.estimated_cost_usd == 0.0007
    assert client.request["max_output_tokens"] == 500
    assert client.request["schema_name"] == "delta_code_dashboard_triage"
    assert "untrusted data" in client.request["system_prompt"]


def test_triage_brief_requires_run_evidence():
    try:
        generate_triage_brief([], client=FakeClient())
    except ValueError as exc:
        assert str(exc) == "at least one verification run is required"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("empty evidence should not call the model")
