import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.control_plane.models import (
    AttemptReview,
    ImpactEvidence,
    MigrationPlan,
    NormalizedChange,
    Recommendation,
)
from app.ingestion.storage import FilesystemArtifactStore
from app.migration_generation.context import assemble_planning_context
from app.migration_generation.executor import (
    CloudflareSandboxExecutor,
    SandboxUnavailable,
    StaticSandboxExecutor,
)
from app.migration_generation.intelligence import (
    MAX_REVIEW_LOG_CHARS,
    OpenAIMigrationIntelligence,
    StaticMigrationIntelligence,
    _execution_for_review,
)
from app.migration_generation.models import (
    GenerationProposal,
    PatchProposal,
    SandboxExecutionResult,
)
from app.migration_generation.policy import (
    PatchPolicyError,
    build_sandbox_request,
    contains_secret,
    normalize_repository_path,
    validate_patch,
)
from app.migration_generation.service import MigrationGenerationService, _guard_recommendation
from app.repository_intelligence.models import RepositoryRef, RepositorySnapshot


def _change() -> NormalizedChange:
    now = datetime(2026, 8, 5, tzinfo=UTC)
    return NormalizedChange(
        id="change-1",
        dedupe_key="provider:change-1",
        provider={"id": "provider", "name": "Provider"},
        status="ready",
        detected_at=now,
        change_type="sdk_symbol_removed",
        severity="high",
        breaking=True,
        summary="Client.send was removed.",
        before={"api_key": "super-secret-provider-value"},
        source_artifacts=[
            {
                "id": "artifact-1",
                "source_type": "sdk_release",
                "canonical_url": "https://provider.example/releases/1",
                "captured_at": now,
                "sha256": "a" * 64,
                "authoritative": True,
            }
        ],
        targets=[{"kind": "symbol", "name": "Client.send"}],
        claims=[
            {
                "id": "claim-1",
                "summary": "The symbol was removed.",
                "provenance": "provider_stated",
                "source_artifact_ids": ["artifact-1"],
            }
        ],
        confidence={"score": 1, "basis": "deterministic"},
    )


def _impact() -> ImpactEvidence:
    return ImpactEvidence(
        assessment_id="impact-1",
        conclusion="affected",
        summary="An affected call site was found.",
        call_sites=[
            {
                "id": "call-1",
                "path": "app.py",
                "start_line": 1,
                "end_line": 1,
                "language": "Python",
                "target": "Client.send",
                "detection_method": "ast",
                "reason": "Matched call.",
                "confidence": {"score": 1, "basis": "deterministic"},
            }
        ],
        coverage={
            "supported": True,
            "languages": ["Python"],
            "files_considered": 1,
            "files_excluded": 0,
            "parse_failures": 0,
            "limitations": [],
        },
        confidence={"score": 1, "basis": "deterministic"},
    )


def _repository() -> RepositoryRef:
    return RepositoryRef(
        id="repo-1",
        workspace_id="workspace-1",
        full_name="acme/example",
        clone_url="https://github.com/acme/example.git",
        default_branch="main",
        installation_id=7,
    )


def _snapshot() -> RepositorySnapshot:
    return RepositorySnapshot(
        id="snapshot-1",
        repository_id="repo-1",
        source_ref="main",
        commit_sha="a" * 40,
        content_digest=f"sha256:{'b' * 64}",
        inventory_digest=f"sha256:{'c' * 64}",
        inventory_version="1.0.0",
        analyzer_versions=["python-ast@1.0.0"],
        created_at=datetime(2026, 8, 5, tzinfo=UTC),
    )


def _plan() -> MigrationPlan:
    return MigrationPlan(
        summary="Replace the removed call.",
        steps=[
            {
                "id": "step-1",
                "description": "Update the client call.",
                "call_site_ids": ["call-1"],
                "expected_paths": ["app.py"],
            }
        ],
        verification_strategy=["Run unit tests."],
        assumptions=[],
        unresolved=[],
    )


def _proposal(content: str, expected_sha256: str) -> PatchProposal:
    return PatchProposal(
        summary="Use Client.send_v2.",
        edits=[
            {
                "path": "app.py",
                "expected_sha256": expected_sha256,
                "content": content,
                "plan_step_ids": ["step-1"],
            }
        ],
        tests=[],
        verification_commands=[
            {"id": "unit", "kind": "unit_test", "argv": ["pytest", "-q"]}
        ],
        generator={"id": "fixture-model", "version": "1.0"},
    )


def _context(root: Path):
    return assemble_planning_context(
        migration_id="migration-1",
        attempt_id="attempt-1",
        previous_attempt_id="attempt-0",
        developer_instructions="Update the internal wrapper first.",
        repository=_repository(),
        snapshot=_snapshot(),
        change=_change(),
        impact=_impact(),
        root=root,
    )


def test_context_only_includes_affected_files_and_redacts_secrets(tmp_path: Path):
    (tmp_path / "app.py").write_text('token = "this-is-a-secret-token-value"\n')
    (tmp_path / "unrelated.py").write_text("safe = True\n")

    context = _context(tmp_path)

    assert [file.path for file in context.files] == ["app.py"]
    assert "this-is-a-secret" not in context.files[0].content
    assert context.files[0].redaction_count == 1
    assert context.change.before == {"api_key": "[REDACTED]"}
    assert context.previous_attempt_id == "attempt-0"
    assert context.developer_instructions == "Update the internal wrapper first."


def test_context_leaves_out_generated_lockfiles_but_keeps_the_manifest(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"dependencies": {"example-sdk": "1.0.0"}}')
    (tmp_path / "package-lock.json").write_text("x" * 700_000)
    impact_payload = _impact().model_dump(mode="json")
    impact_payload["call_sites"] = []
    impact_payload["dependency_matches"] = [
        {
            "ecosystem": "npm",
            "package": "example-sdk",
            "resolved_version": "1.0.0",
            "source_path": path,
            "detection_method": method,
            "evidence": "Declared provider dependency.",
        }
        for path, method in (("package.json", "manifest"), ("package-lock.json", "lockfile"))
    ]

    context = assemble_planning_context(
        migration_id="migration-1",
        attempt_id="attempt-1",
        repository=_repository(),
        snapshot=_snapshot(),
        change=_change(),
        impact=ImpactEvidence.model_validate(impact_payload),
        root=tmp_path,
    )

    assert [item.path for item in context.files] == ["package.json"]


@pytest.mark.parametrize(
    "line",
    [
        'client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))',
        'openai.api_key = os.getenv("OPENAI_API_KEY")',
        'api_key=os.environ["OPENAI_API_KEY"]',
        "client = OpenAI(api_key=settings.openai_api_key)",
        "api_key=self.config.api_key,",
        "client = OpenAI(api_key=openai_api_key)",
        "token = get_access_token()",
        "access_token = response.json()",
        'password = request.form["password"]',
        "secret = config.secret_value",
        "token: Optional[str] = None",
        "api_key: SecretStr",
        '{"api_key": os.environ.get("KEY")}',
    ],
)
def test_code_that_reads_a_credential_is_not_a_credential(line):
    assert contains_secret(line) is False


@pytest.mark.parametrize(
    "line",
    [
        'api_key = "abcd1234efgh5678"',
        "api_key='abcd1234efgh5678'",
        'password = "correcthorsebattery"',
        'token: "short.dotted.value"',
        "password: hunter2hunter2",
        "token=abcd1234efgh5678",
        "SECRET=Abc123Def456Ghi",
        "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N",
        "token: eyJhbGci.eyJzdWIi.abcdefgh",
        "api_key = abc123def.ghi456jkl",
        'key = "sk-abcdefghijklmnopqrstuvwxyz123456"',
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "-----BEGIN RSA PRIVATE KEY-----",
        'password = "os.getenv(not_really_code)"',
    ],
)
def test_real_credentials_are_still_caught(line):
    assert contains_secret(line) is True


def test_patch_cannot_write_a_redaction_marker_into_a_file(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 1\n")
    digest = hashlib.sha256(b"value = 1\n").hexdigest()

    with pytest.raises(PatchPolicyError, match="redaction marker"):
        validate_patch(tmp_path, _plan(), _proposal("value = [REDACTED]\n", digest))


def _big_file(tmp_path: Path, lines: int) -> str:
    content = "".join(f"value_{index} = {index}\n" for index in range(lines))
    (tmp_path / "app.py").write_text(content)
    return hashlib.sha256(content.encode()).hexdigest()


@pytest.mark.parametrize(
    ("original", "kept"),
    [(581, 15), (674, 251), (254, 193), (100, 79)],
)
def test_patch_that_drops_much_of_a_file_is_rejected(tmp_path: Path, original, kept):
    digest = _big_file(tmp_path, original)
    partial = "".join(f"value_{index} = {index}\n" for index in range(kept))

    with pytest.raises(PatchPolicyError, match="removes"):
        validate_patch(tmp_path, _plan(), _proposal(partial, digest))


@pytest.mark.parametrize(
    ("original", "kept"),
    [(100, 100), (100, 105), (100, 81), (30, 11), (20, 1)],
)
def test_ordinary_edits_and_small_removals_are_allowed(tmp_path: Path, original, kept):
    digest = _big_file(tmp_path, original)
    edited = "".join(f"value_{index} = {index}\n" for index in range(kept))

    validate_patch(tmp_path, _plan(), _proposal(edited, digest))


def test_prompts_send_unicode_text_rather_than_escapes(tmp_path: Path):
    class RecordingClient:
        available = True
        model = "gpt-4o"
        usage = None

        def __init__(self) -> None:
            self.user_input = ""

        def generate_json(self, *, user_input, **_options):
            self.user_input = user_input
            raise RuntimeError("stop after capturing the prompt")

    (tmp_path / "app.py").write_text("note = 'caf\u00e9 \u2014 \u2713'\n")
    client = RecordingClient()
    intelligence = OpenAIMigrationIntelligence(client)

    with pytest.raises(RuntimeError):
        intelligence.propose(_context(tmp_path))

    assert "caf\u00e9 \u2014 \u2713" in client.user_input
    assert "\\u2014" not in client.user_input


def test_review_logs_are_bounded_and_keep_the_end_of_the_output():
    execution = SandboxExecutionResult(
        attempt_id="attempt-1",
        status="failed",
        checks=[
            {"id": "tests", "kind": "unit_test", "status": "failed", "command": "pytest",
             "exit_code": 1, "duration_ms": 1,
             "stdout": "x" * 19_990 + "FAILED_END", "stderr": ""}
        ],
        executor={"id": "local-docker", "version": "1.0.0"},
        duration_ms=1,
        network_policy="deny_all",
        destroyed=True,
    )

    logged = _execution_for_review(execution)["checks"][0]["stdout"]

    assert len(logged) == MAX_REVIEW_LOG_CHARS // 2
    assert logged.endswith("FAILED_END")
    assert len(execution.checks[0].stdout) == 20_000


def _guard_execution(*checks) -> SandboxExecutionResult:
    return SandboxExecutionResult(
        attempt_id="attempt-1",
        status="passed",
        checks=[
            {"id": f"check-{index}", "kind": kind, "status": "passed", "command": command,
             "exit_code": 0, "duration_ms": 1}
            for index, (kind, command) in enumerate(checks)
        ],
        executor={"id": "local-docker", "version": "1.0.0"},
        duration_ms=1,
        network_policy="deny_all",
        destroyed=True,
    )


def _approve() -> Recommendation:
    return Recommendation(
        action="approve",
        rationale="All checks passed.",
        confidence={"score": 1, "basis": "inferred"},
        unresolved=[],
    )


@pytest.mark.parametrize(
    "checks",
    [
        [("other", "python --version")],
        [("unit_test", "python --version")],
        [("unit_test", "pytest --help")],
        [("lint", "ruff check .")],
        [("format", "ruff format --check .")],
    ],
)
def test_checks_that_cannot_fail_on_the_patch_do_not_earn_approval(checks):
    guarded = _guard_recommendation(_approve(), _guard_execution(*checks))

    assert guarded.action == "revise"
    assert "no check exercised" in guarded.rationale


@pytest.mark.parametrize(
    "checks",
    [
        [("unit_test", "pytest -q")],
        [("lint", "ruff check ."), ("type_check", "mypy app")],
        [("build", "npm run build")],
    ],
)
def test_a_check_that_exercises_the_code_keeps_approval(checks):
    assert _guard_recommendation(_approve(), _guard_execution(*checks)).action == "approve"


def test_context_includes_dependency_evidence_file(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("example-sdk==1.0.0\n")
    impact_payload = _impact().model_dump(mode="json")
    impact_payload["call_sites"] = []
    impact_payload["dependency_matches"] = [
        {
            "ecosystem": "pypi",
            "package": "example-sdk",
            "resolved_version": "1.0.0",
            "source_path": "requirements.txt",
            "detection_method": "manifest",
            "evidence": "Declared provider dependency.",
        }
    ]

    context = assemble_planning_context(
        migration_id="migration-1",
        attempt_id="attempt-1",
        repository=_repository(),
        snapshot=_snapshot(),
        change=_change(),
        impact=ImpactEvidence.model_validate(impact_payload),
        root=tmp_path,
    )

    assert [file.path for file in context.files] == ["requirements.txt"]


@pytest.mark.parametrize(
    "path",
    ["../secret", "/etc/passwd", ".git/config", ".github/workflows/release.yml", ".env"],
)
def test_patch_policy_rejects_paths_outside_the_safe_surface(path: str):
    with pytest.raises(PatchPolicyError):
        normalize_repository_path(path)


def test_patch_validation_rejects_stale_base_digest(tmp_path: Path):
    (tmp_path / "app.py").write_text("old()\n")
    proposal = _proposal("new()\n", "0" * 64)

    with pytest.raises(PatchPolicyError, match="stale"):
        validate_patch(tmp_path, _plan(), proposal)


def test_patch_validation_rejects_secret_like_generated_output(tmp_path: Path):
    source = b"old()\n"
    (tmp_path / "app.py").write_bytes(source)
    proposal = _proposal(
        'api_key = "generated-secret-value"\n',
        hashlib.sha256(source).hexdigest(),
    )

    with pytest.raises(PatchPolicyError, match="credential-like"):
        validate_patch(tmp_path, _plan(), proposal)


def test_sandbox_bundle_excludes_credential_files_and_secret_text(tmp_path: Path):
    source = b"old()\n"
    (tmp_path / "app.py").write_bytes(source)
    (tmp_path / ".env").write_text("TOKEN=not-for-the-sandbox\n")
    (tmp_path / "credentials.txt").write_text('api_key = "super-secret-value"\n')
    proposal = _proposal("new()\n", hashlib.sha256(source).hexdigest())
    _, patch = validate_patch(tmp_path, _plan(), proposal)

    request = build_sandbox_request(
        tmp_path,
        "attempt-1",
        _snapshot().content_digest,
        patch,
        proposal,
    )

    assert [file.path for file in request.files] == ["app.py"]


def test_service_prevents_approval_when_sandbox_teardown_is_unconfirmed(tmp_path: Path):
    root = tmp_path / "repository"
    root.mkdir()
    source = b"old()\n"
    (root / "app.py").write_bytes(source)
    proposal = _proposal("new()\n", hashlib.sha256(source).hexdigest())
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
                "duration_ms": 10,
            }
        ],
        executor={"id": "cloudflare-sandbox", "version": "0.12.4"},
        duration_ms=10,
        network_policy="deny_all",
        destroyed=False,
    )
    review = AttemptReview(
        summary="The patch is correct.",
        findings=[],
        provenance="model_inferred",
        model={"id": "fixture-reviewer", "version": "1.0"},
    )
    recommendation = Recommendation(
        action="approve",
        rationale="All checks passed.",
        confidence={"score": 1, "basis": "inferred"},
        unresolved=[],
    )
    service = MigrationGenerationService(
        StaticMigrationIntelligence(
            GenerationProposal(plan=_plan(), patch=proposal),
            review,
            recommendation,
        ),
        StaticSandboxExecutor(execution),
        FilesystemArtifactStore(tmp_path / "artifacts"),
    )

    result = service.run(_context(root), root)

    assert result.evidence.recommendation.action == "snooze"
    assert any(
        "destroyed: False" in item
        for item in result.evidence.recommendation.confidence.unresolved
    )


def test_cloudflare_executor_rejects_cross_attempt_response(tmp_path: Path):
    response = {
        "schema_version": "1.0",
        "attempt_id": "another-attempt",
        "status": "passed",
        "checks": [
            {
                "id": "unit",
                "kind": "unit_test",
                "status": "passed",
                "command": "pytest -q",
                "exit_code": 0,
                "duration_ms": 1,
            }
        ],
        "executor": {"id": "cloudflare-sandbox", "version": "0.12.4"},
        "duration_ms": 1,
        "network_policy": "deny_all",
        "destroyed": True,
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
    )
    executor = CloudflareSandboxExecutor(
        "https://sandbox.example",
        "token",
        enabled=True,
        client=client,
    )
    source = b"old()\n"
    proposal = _proposal("new()\n", hashlib.sha256(source).hexdigest())
    with pytest.raises(SandboxUnavailable, match="attempt"):
        (tmp_path / "app.py").write_bytes(source)
        _, patch = validate_patch(tmp_path, _plan(), proposal)
        request = build_sandbox_request(
            tmp_path,
            "attempt-1",
            _snapshot().content_digest,
            patch,
            proposal,
        )
        executor.execute(request)


def test_cloudflare_executor_requires_explicit_enablement():
    with pytest.raises(SandboxUnavailable, match="disabled"):
        CloudflareSandboxExecutor("https://sandbox.example", "token", enabled=False)
