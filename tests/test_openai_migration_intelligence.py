from types import SimpleNamespace

from app.control_plane.models import MigrationPlan, PatchEvidence
from app.migration_generation.intelligence import OpenAIMigrationIntelligence
from app.migration_generation.models import SandboxExecutionResult
from app.openai_responses import OpenAIUsage


class FakeResponsesClient:
    available = True
    model = "gpt-4o"
    usage = OpenAIUsage(input_tokens=200, output_tokens=50, cost_usd=0.001)

    def __init__(self):
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["schema_name"] == "delta_code_migration_proposal":
            return {
                "plan": {
                    "summary": "Replace the removed call.",
                    "steps": [
                        {
                            "id": "step-1",
                            "description": "Update the client call.",
                            "call_site_ids": ["call-1"],
                            "expected_paths": ["app.py"],
                        }
                    ],
                    "verification_strategy": ["Run tests."],
                    "assumptions": [],
                    "unresolved": [],
                },
                "patch": {
                    "summary": "Use the replacement call.",
                    "edits": [
                        {
                            "path": "app.py",
                            "expected_sha256": "a" * 64,
                            "content": "replacement()\n",
                            "plan_step_ids": ["step-1"],
                        }
                    ],
                    "tests": [],
                    "verification_commands": [
                        {"id": "unit", "kind": "unit_test", "argv": ["pytest", "-q"]}
                    ],
                    "unresolved": [],
                    "generator": {"id": "untrusted", "version": "untrusted"},
                },
            }
        return {
            "review": {
                "summary": "Sandbox checks passed.",
                "findings": [],
                "provenance": "model_inferred",
                "model": {"id": "untrusted", "version": "untrusted"},
            },
            "recommendation": {
                "action": "approve",
                "rationale": "All supplied checks passed.",
                "confidence": {"score": 0.9, "basis": "inferred"},
                "unresolved": [],
            },
        }


def test_openai_migration_intelligence_uses_structured_proposal_and_review():
    client = FakeResponsesClient()
    intelligence = OpenAIMigrationIntelligence(client)
    context = SimpleNamespace(model_dump=lambda **_kwargs: {"untrusted_content_notice": "data"})

    proposal = intelligence.propose(context)
    execution = SandboxExecutionResult(
        attempt_id="attempt-1",
        status="passed",
        checks=[
            {
                "id": "unit",
                "kind": "unit_test",
                "status": "passed",
                "command": "pytest -q",
                "exit_code": 0,
                "duration_ms": 1,
            }
        ],
        executor={"id": "sandbox", "version": "1"},
        duration_ms=1,
        network_policy="deny_all",
        destroyed=True,
    )
    patch = PatchEvidence(
        artifact_id="patch-1",
        sha256="b" * 64,
        summary="Patch",
        files=[
            {
                "path": "app.py",
                "change_type": "modified",
                "plan_step_ids": ["step-1"],
            }
        ],
    )
    review, recommendation = intelligence.review(
        context,
        MigrationPlan.model_validate(proposal.plan),
        patch,
        execution,
    )

    assert [call["schema_name"] for call in client.calls] == [
        "delta_code_migration_proposal",
        "delta_code_migration_review",
    ]
    assert client.calls[0]["max_output_tokens"] == 6_000
    assert client.calls[1]["max_output_tokens"] == 3_000
    assert proposal.patch.generator.id == "gpt-4o"
    assert review.model.id == "gpt-4o"
    assert recommendation.action == "approve"
