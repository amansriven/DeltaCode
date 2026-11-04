from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.hardening import router as hardening_router
from app.hardening.benchmark import run_benchmark
from app.hardening.limits import BudgetExceeded, GenerationLimits
from app.hardening.metrics import MetricsRegistry, registry
from app.main import app
from app.migration_generation.models import GenerationProposal, PatchProposal


def test_labeled_repository_benchmark_meets_release_thresholds():
    score = run_benchmark(Path("benchmarks/repository-impact-v1.json"))

    assert score.cases == 7
    assert score.precision >= 0.9
    assert score.recall >= 0.9
    assert score.classifications["typescript-negative-uncertain"] == "uncertain"
    assert score.classifications["unsupported-go-negative"] == "unsupported"


def test_adversarial_benchmark_v2_holds_its_measured_baseline():
    """v2 labels ground truth; the floor guards the analyzer fixes it motivated."""
    score = run_benchmark(Path("benchmarks/repository-impact-v2.json"))

    assert score.cases >= 300
    assert score.precision >= 0.98
    assert score.recall >= 0.95
    assert score.by_difficulty["easy"]["fp"] == 0
    assert score.by_difficulty["easy"]["fn"] == 0
    assert score.by_category["dependency"]["fn"] == 0


def test_held_out_benchmark_holds_its_measured_baseline():
    """Written after the fixes, so it measures generalization rather than tuning."""
    score = run_benchmark(Path("benchmarks/repository-impact-holdout.json"))

    assert score.cases >= 70
    assert score.precision >= 0.95
    assert score.recall >= 0.55


def test_generation_limits_reject_excessive_aggregate_check_timeout():
    patch = PatchProposal(
        summary="Bounded proposal.",
        edits=[
            {
                "path": "app.py",
                "content": "value = 1\n",
                "plan_step_ids": ["step-1"],
            }
        ],
        verification_commands=[
            {
                "id": "tests",
                "kind": "unit_test",
                "argv": ["pytest"],
                "timeout_ms": 120_000,
            }
        ],
        generator={"id": "fixture", "version": "1"},
    )
    proposal = GenerationProposal(
        plan={
            "summary": "Update the call.",
            "steps": [
                {
                    "id": "step-1",
                    "description": "Update app.py.",
                    "call_site_ids": [],
                    "expected_paths": ["app.py"],
                }
            ],
            "verification_strategy": ["pytest"],
            "assumptions": [],
            "unresolved": [],
        },
        patch=patch,
    )

    with pytest.raises(BudgetExceeded, match="timeouts"):
        GenerationLimits(max_total_check_timeout_ms=60_000).validate_proposal(proposal)


def test_generation_limits_fail_fast_on_invalid_environment(monkeypatch):
    monkeypatch.setenv("GENERATION_MAX_CONTEXT_BYTES", "unbounded")

    with pytest.raises(ValueError, match="must be an integer"):
        GenerationLimits.from_env()


def test_metrics_registry_has_fixed_labels_and_prometheus_output():
    metrics = MetricsRegistry()
    metrics.record_job("generation", "completed", 1.25)

    output = metrics.render_prometheus()

    assert 'delta_code_jobs_total{kind="generation",status="completed"} 1' in output
    assert 'delta_code_job_duration_seconds_total{kind="generation"} 1.250000' in output
    with pytest.raises(ValueError, match="fixed"):
        metrics.record_job("repository-name", "completed", 1)


def test_metrics_endpoint_is_bearer_protected(monkeypatch):
    registry.reset()
    registry.record_job("ingestion", "failed", 0.5)
    monkeypatch.setenv("METRICS_BEARER_TOKEN", "metrics-secret")
    client = TestClient(app)

    denied = client.get("/metrics")
    allowed = client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert 'delta_code_jobs_total{kind="ingestion",status="failed"} 1' in allowed.text


def test_readiness_reports_database_failure_without_exposing_details(monkeypatch):
    monkeypatch.setattr(hardening_router, "_database_ready", lambda: False)

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"] is False
