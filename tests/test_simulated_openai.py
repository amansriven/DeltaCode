import json

import httpx
import pytest

from app.hardening import simulated_openai
from app.hardening.simulated_openai import SimulatedResponsesAPI
from app.migration_generation.intelligence import OpenAIMigrationIntelligence
from app.migration_generation.models import ModelGenerationProposal
from app.migration_generation.policy import PatchPolicyError
from app.openai_responses import OpenAIResponsesClient, ProcessBudget, strict_json_schema
from tests.test_migration_generation import _context

DATASET = {"cases": [{"repository": "acme/example", "migration": "numpy-1.24-aliases"}]}


def _intelligence(simulator, limit=1.0) -> OpenAIMigrationIntelligence:
    return OpenAIMigrationIntelligence(
        OpenAIResponsesClient(
            api_key="simulated",
            client=httpx.Client(transport=httpx.MockTransport(simulator.handle)),
            budget=ProcessBudget(limit, limit),
        )
    )


def _without_faults(monkeypatch):
    monkeypatch.setattr(SimulatedResponsesAPI, "_fault", lambda self, repository: None)


def test_real_client_and_schema_produce_a_migrated_file(monkeypatch, tmp_path):
    _without_faults(monkeypatch)
    (tmp_path / "app.py").write_text("a = np.float(1)\nb = np.float64(2)\n")
    simulator = SimulatedResponsesAPI(DATASET)
    intelligence = _intelligence(simulator)

    proposal = intelligence.propose(_context(tmp_path))

    assert proposal.patch.edits[0].content == "a = float(1)\nb = np.float64(2)\n"
    assert intelligence.usage.cost_usd > 0
    assert simulator.requests[0]["name"] == "delta_code_migration_proposal"


def test_broad_alias_mistake_is_contained_by_token_matching(monkeypatch, tmp_path):
    monkeypatch.setattr(SimulatedResponsesAPI, "_fault", lambda self, repository: "broad_alias")
    (tmp_path / "app.py").write_text("a = np.float(1)\nb = np.float64(2)\n")

    proposal = _intelligence(SimulatedResponsesAPI(DATASET)).propose(_context(tmp_path))

    assert "np.float64(2)" in proposal.patch.edits[0].content


def test_retyped_text_is_refused_and_the_paid_answer_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(SimulatedResponsesAPI, "_fault", lambda self, repository: "retyped_quotes")
    (tmp_path / "app.py").write_text("a = np.float(1)\n")
    intelligence = _intelligence(SimulatedResponsesAPI(DATASET))

    with pytest.raises(PatchPolicyError, match="was not found"):
        intelligence.propose(_context(tmp_path))

    assert intelligence.last_proposal is not None


def test_answer_longer_than_the_output_limit_fails_as_truncated(monkeypatch, tmp_path):
    _without_faults(monkeypatch)
    monkeypatch.setattr(simulated_openai, "EXTRA_OUTPUT_TOKENS", 100_000)
    (tmp_path / "app.py").write_text("a = np.float(1)\n")

    with pytest.raises(Exception, match="incomplete"):
        _intelligence(SimulatedResponsesAPI(DATASET)).propose(_context(tmp_path))


def test_budget_is_enforced_as_in_a_paid_run(monkeypatch, tmp_path):
    _without_faults(monkeypatch)
    (tmp_path / "app.py").write_text("a = np.float(1)\n")

    with pytest.raises(Exception, match="budget"):
        _intelligence(SimulatedResponsesAPI(DATASET), limit=0.0001).propose(_context(tmp_path))


def test_model_schema_follows_the_strict_output_rules():
    """Strict mode rejects a request outright if any object breaks these, stopping a whole run."""

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                walk(value)

    schema = strict_json_schema(ModelGenerationProposal.model_json_schema())
    walk(schema)
    assert json.dumps(schema)
