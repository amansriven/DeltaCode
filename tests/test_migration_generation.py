import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.control_plane.models import (
    AttemptReview,
    ComponentVersion,
    DependencyMatch,
    ImpactEvidence,
    MigrationPlan,
    NormalizedChange,
    PatchEvidence,
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
    ModelGenerationProposal,
    PatchProposal,
    SandboxExecutionResult,
)
from app.migration_generation.policy import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_FILES,
    PatchPolicyError,
    ReplacementError,
    build_sandbox_request,
    collect_sandbox_files,
    contains_secret,
    materialize_proposal,
    normalize_repository_path,
    validate_patch,
)
from app.migration_generation.service import MigrationGenerationService, _guard_recommendation
from app.migration_generation.verification import add_python_checks
from app.openai_responses import strict_json_schema
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


GENERATOR = ComponentVersion(id="fixture-model", version="1.0")


def _model_proposal(*replacements: dict, path: str = "app.py") -> ModelGenerationProposal:
    return ModelGenerationProposal(
        plan=_plan(),
        patch={
            "summary": "Use Client.send_v2.",
            "edits": [
                {
                    "path": path,
                    "replacements": [
                        {"replace_all": False, **replacement} for replacement in replacements
                    ],
                    "plan_step_ids": ["step-1"],
                }
            ],
            "verification_commands": [
                {"id": "unit", "kind": "unit_test", "argv": ["pytest", "-q"]}
            ],
        },
    )


def test_replacements_keep_every_unmatched_character(tmp_path: Path):
    source = "# caf\u00e9 \u2713\nclient.send(1)\n\nkeep   =  'spacing'  \n"
    (tmp_path / "app.py").write_text(source)
    context = _context(tmp_path)

    proposal = materialize_proposal(
        context,
        _model_proposal({"old_text": "client.send(1)", "new_text": "client.send_v2(1)"}),
        GENERATOR,
    )

    edit = proposal.patch.edits[0]
    assert edit.content == source.replace("client.send(1)", "client.send_v2(1)")
    assert edit.expected_sha256 == context.files[0].sha256
    assert proposal.patch.generator == GENERATOR
    validate_patch(tmp_path, proposal.plan, proposal.patch)


def test_a_large_file_needs_no_retyping(tmp_path: Path):
    padding = "".join(f"value_{index} = {index}\n" for index in range(1500))
    (tmp_path / "app.py").write_text(f"client.send(1)\n{padding}")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "client.send(1)", "new_text": "client.send_v2(1)"}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == f"client.send_v2(1)\n{padding}"


def test_replacement_matching_many_places_needs_replace_all(tmp_path: Path):
    (tmp_path / "app.py").write_text("client.send(1)\nclient.send(1)\n")
    context = _context(tmp_path)

    with pytest.raises(PatchPolicyError, match="matches 2 places"):
        materialize_proposal(
            context, _model_proposal({"old_text": ".send(", "new_text": ".send_v2("}), GENERATOR
        )

    proposal = materialize_proposal(
        context,
        _model_proposal({"old_text": ".send(", "new_text": ".send_v2(", "replace_all": True}),
        GENERATOR,
    )
    assert proposal.patch.edits[0].content == "client.send_v2(1)\nclient.send_v2(1)\n"


def test_replacements_apply_in_order(tmp_path: Path):
    (tmp_path / "app.py").write_text("client.send(1)\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal(
            {"old_text": "send(1)", "new_text": "send_v2(1)"},
            {"old_text": "send_v2(1)", "new_text": "send_v2(2)"},
        ),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == "client.send_v2(2)\n"


def test_replacement_cannot_rewrite_a_longer_name(tmp_path: Path):
    source = "a = np.float(1)\nb = np.float64(1)\nc = np.float_(1)\nd = np.floating\n"
    (tmp_path / "app.py").write_text(source)

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "np.float", "new_text": "float", "replace_all": True}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == source.replace("np.float(", "float(")


def test_whole_line_copy_is_not_ambiguous_with_longer_lines(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = run(1)\nif a:\n    x = run(1)\nend = x = run(1)\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "x = run(1)", "new_text": "x = go(1)"}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == "x = go(1)\nif a:\n    x = run(1)\nend = x = run(1)\n"


def test_ambiguous_text_tells_the_model_how_to_fix_it(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = run(1)\nx = run(1)\n")

    with pytest.raises(ReplacementError, match="include more surrounding lines"):
        materialize_proposal(
            _context(tmp_path),
            _model_proposal({"old_text": "x = run(1)", "new_text": "x = go(1)"}),
            GENERATOR,
        )


def test_replacement_ignores_differences_in_trailing_spaces(tmp_path: Path):
    (tmp_path / "app.py").write_text("first = 1   \nclient.send(1)\t\nend = 1\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "first = 1\nclient.send(1)  ", "new_text": "changed = 2"}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == "changed = 2\t\nend = 1\n"


def test_replacement_text_is_inserted_literally(tmp_path: Path):
    (tmp_path / "app.py").write_text("client.send(1)\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "client.send(1)", "new_text": r"path = '\1\g<0>\n'"}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == "path = '\\1\\g<0>\\n'\n"


def test_added_lines_follow_the_files_line_endings(tmp_path: Path):
    (tmp_path / "app.py").write_bytes(b"import a\r\nclient.send(1)\r\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "import a", "new_text": "import a\nimport b"}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == "import a\r\nimport b\r\nclient.send(1)\r\n"


def test_replacement_keeps_windows_line_endings(tmp_path: Path):
    (tmp_path / "app.py").write_bytes(b"import a\r\nclient.send(1)\r\nend = 1\r\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal(
            {"old_text": "import a\nclient.send(1)", "new_text": "import b\nclient.send_v2(1)"}
        ),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == "import b\r\nclient.send_v2(1)\r\nend = 1\r\n"


@pytest.mark.parametrize(
    ("replacement", "path", "message"),
    [
        ({"old_text": "absent()", "new_text": "x"}, "app.py", "was not found"),
        ({"old_text": "", "new_text": "x"}, "app.py", "no text to match"),
        ({"old_text": "client", "new_text": "x"}, "other.py", "was not supplied"),
    ],
)
def test_replacement_that_cannot_apply_is_rejected(tmp_path: Path, replacement, path, message):
    (tmp_path / "app.py").write_text("client.send(1)\n")

    with pytest.raises(PatchPolicyError, match=message):
        materialize_proposal(
            _context(tmp_path), _model_proposal(replacement, path=path), GENERATOR
        )


def test_empty_old_text_adds_a_file_the_context_does_not_have(tmp_path: Path):
    (tmp_path / "app.py").write_text("client.send(1)\n")

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "", "new_text": "new = 1\n"}, path="added.py"),
        GENERATOR,
    )

    edit = proposal.patch.edits[0]
    assert (edit.path, edit.content, edit.expected_sha256) == ("added.py", "new = 1\n", None)


def test_redacted_text_cannot_be_written_back_through_a_replacement(tmp_path: Path):
    (tmp_path / "app.py").write_text('password = "correcthorsebattery"\nclient.send(1)\n')

    proposal = materialize_proposal(
        _context(tmp_path),
        _model_proposal({"old_text": "client.send(1)", "new_text": "client.send_v2(1)"}),
        GENERATOR,
    )

    with pytest.raises(PatchPolicyError, match="redaction marker"):
        validate_patch(tmp_path, proposal.plan, proposal.patch)


def _impact_at(line: int, path: str = "app.py") -> ImpactEvidence:
    impact = _impact()
    site = impact.call_sites[0].model_copy(
        update={"path": path, "start_line": line, "end_line": line}
    )
    return impact.model_copy(update={"call_sites": [site]})


def _context_with(root: Path, impact: ImpactEvidence):
    return assemble_planning_context(
        migration_id="migration-1",
        attempt_id="attempt-1",
        repository=_repository(),
        snapshot=_snapshot(),
        change=_change(),
        impact=impact,
        root=root,
    )


def _large_source(call_line: int, total: int = 4000) -> str:
    lines = [f"value_{index} = 'padding padding padding'\n" for index in range(total)]
    lines[call_line - 1] = "client.send(1)\n"
    return "".join(lines)


def test_large_file_is_windowed_around_call_sites_and_restored_exactly(tmp_path: Path):
    source = _large_source(2000)
    (tmp_path / "app.py").write_text(source)

    context = _context_with(tmp_path, _impact_at(2000))

    supplied = context.files[0]
    assert len(supplied.content) < 5000
    assert "value_0 =" in supplied.content
    assert "client.send(1)" in supplied.content
    assert "value_1000 =" not in supplied.content
    assert "omitted lines 31-1984" in supplied.content
    assert "value_1000" not in str(context.model_dump(mode="json"))
    assert supplied.sha256 == hashlib.sha256(source.encode()).hexdigest()

    proposal = materialize_proposal(
        context,
        _model_proposal({"old_text": "client.send(1)", "new_text": "client.send_v2(1)"}),
        GENERATOR,
    )

    assert proposal.patch.edits[0].content == source.replace("client.send(1)", "client.send_v2(1)")
    validate_patch(tmp_path, proposal.plan, proposal.patch)


def test_files_too_large_to_return_are_refused_before_any_spend(tmp_path: Path):
    (tmp_path / "app.py").write_text(_large_source(2000, total=30_000))

    with pytest.raises(PatchPolicyError, match="too large to patch"):
        _context_with(tmp_path, _impact_at(2000))


def test_small_file_is_sent_whole(tmp_path: Path):
    source = _large_source(50, total=100)
    (tmp_path / "app.py").write_text(source)

    assert _context_with(tmp_path, _impact_at(50)).files[0].content == source


@pytest.mark.parametrize(
    "replacement",
    [
        {"old_text": "<<<delta-code omitted lines 31-1984>>>\n", "new_text": ""},
        {"old_text": "client.send(1)", "new_text": "<<<delta-code omitted lines 31-1984>>>\n"},
    ],
)
def test_replacement_cannot_delete_or_duplicate_omitted_lines(tmp_path: Path, replacement):
    (tmp_path / "app.py").write_text(_large_source(2000))
    context = _context_with(tmp_path, _impact_at(2000))

    with pytest.raises(PatchPolicyError, match="crosses omitted lines"):
        materialize_proposal(context, _model_proposal(replacement), GENERATOR)


def test_manifests_nearest_the_root_fit_within_the_file_limit(tmp_path: Path):
    (tmp_path / "app.py").write_text("client.send(1)\n")
    impact = _impact_at(1)
    match = DependencyMatch(
        ecosystem="pypi", package="acme", detection_method="manifest", evidence="acme==1"
    )
    matches = []
    for index in range(30):
        directory = tmp_path / "packages" / f"p{index:02}"
        directory.mkdir(parents=True)
        (directory / "requirements.txt").write_text("acme==1\n")
        matches.append(
            match.model_copy(update={"source_path": f"packages/p{index:02}/requirements.txt"})
        )
    (tmp_path / "requirements.txt").write_text("acme==1\n")
    matches.append(match.model_copy(update={"source_path": "requirements.txt"}))

    context = _context_with(tmp_path, impact.model_copy(update={"dependency_matches": matches}))

    paths = {item.path for item in context.files}
    assert len(paths) == 20
    assert {"app.py", "requirements.txt"} <= paths


def test_sandbox_bundle_keeps_everything_when_the_repository_fits(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")

    assert set(collect_sandbox_files(tmp_path)) == {"app.py", "logo.png"}


def test_oversized_repository_drops_assets_and_very_large_files(tmp_path: Path):
    (tmp_path / "app.py").write_text("x = 1\n")
    (tmp_path / "video.mp4").write_bytes(b"0" * (MAX_BUNDLE_BYTES + 1))
    (tmp_path / "generated.py").write_text("y = 1\n" * 50_000)

    assert set(collect_sandbox_files(tmp_path)) == {"app.py"}


def test_repository_still_too_large_after_trimming_is_refused(tmp_path: Path):
    for index in range(MAX_BUNDLE_FILES + 1):
        (tmp_path / f"module_{index}.py").write_text("x = 1\n")

    with pytest.raises(PatchPolicyError, match="sandbox upload limits"):
        collect_sandbox_files(tmp_path)


def _execution(request, status="passed", exit_code=0, stdout="") -> SandboxExecutionResult:
    return SandboxExecutionResult(
        attempt_id=request.attempt_id,
        status=status,
        checks=[
            {
                "id": check.id,
                "kind": check.kind,
                "status": status,
                "command": " ".join(check.argv),
                "exit_code": exit_code,
                "duration_ms": 1,
                "stdout": stdout,
            }
            for check in request.checks
        ],
        executor={"id": "fake", "version": "1"},
        duration_ms=1,
        network_policy="deny_all",
        destroyed=True,
    )


class RecordingModel:
    """Answers each proposal from a script and keeps every prompt it was given."""

    def __init__(self, tmp_path: Path, *answers) -> None:
        self.answers = list(answers)
        self.prompts = []
        self.reviewed = None

    def propose(self, context):
        self.prompts.append(context)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return GenerationProposal(plan=_plan(), patch=_proposal(answer, context.files[0].sha256))

    def review(self, context, plan, patch, execution):
        self.reviewed = context
        return (
            AttemptReview(
                summary="Reviewed.",
                findings=[],
                provenance="model_inferred",
                model=ComponentVersion(id="fake", version="1"),
            ),
            Recommendation(
                action="approve",
                rationale="Checks passed.",
                confidence={"score": 1, "basis": "inferred"},
                unresolved=[],
            ),
        )


class ScriptedExecutor:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return _execution(request, **self.results.pop(0))


def _service(tmp_path: Path, model, executor) -> MigrationGenerationService:
    return MigrationGenerationService(
        model, executor, FilesystemArtifactStore(tmp_path / "artifacts")
    )


REGRESSION = {"status": "failed", "exit_code": 1, "stdout": "REGRESSION app.py no longer imports"}


def test_python_patch_runs_delta_code_checks_instead_of_the_models(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    executor = ScriptedExecutor({})
    model = RecordingModel(tmp_path, "client.send_v2(1)\n")

    result = _service(tmp_path, model, executor).run(
        _context(root), root, baseline_tests={"test_app.py::test_go"}
    )

    request = executor.requests[0]
    assert [check.id for check in request.checks] == ["delta-code-imports", "delta-code-tests"]
    bundled = {item.path: item for item in request.files}
    assert {".delta-code/verify_python.py", ".delta-code/baseline-tests.json"} <= set(bundled)
    originals = json.loads(
        base64.b64decode(bundled[".delta-code/originals.json"].content_base64)
    )
    assert base64.b64decode(originals["app.py"]) == b"client.send(1)\n"
    assert result.attempts == 1
    assert result.evidence.recommendation.action == "approve"


def test_disallowed_model_command_is_dropped_instead_of_wasting_the_patch(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.js").write_text("client.send(1)\n")
    (root / "app.py").write_text("client.send(1)\n")
    executor = ScriptedExecutor({})

    class BadCommandModel(RecordingModel):
        def propose(self, context):
            proposal = super().propose(context)
            patch = proposal.patch.model_copy(
                update={
                    "verification_commands": [
                        {"id": "bad", "kind": "unit_test", "argv": ["./run_tests.sh"]}
                    ]
                }
            )
            return GenerationProposal.model_validate(
                {"plan": proposal.plan.model_dump(), "patch": patch.model_dump()}
            )

    result = _service(tmp_path, BadCommandModel(tmp_path, "client.send_v2(1)\n"), executor).run(
        _context(root), root
    )

    assert result.attempts == 1
    assert "./run_tests.sh" not in str(executor.requests[0].checks)


def test_import_check_compares_with_files_left_out_of_the_bundle(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text('client.send(1)\ntoken = "abcdefghijklmnop"\n')
    context = _context(root)
    (root / "app.py").write_text('client.send(1)\ntoken = "abcdefghijklmnop"\n')
    proposal = _proposal("client.send_v2(1)\n", context.files[0].sha256)
    request = build_sandbox_request(
        root,
        "attempt-1",
        "sha256:" + "0" * 64,
        PatchEvidence(
            artifact_id="patch-1",
            sha256="1" * 64,
            summary="Patch",
            files=[{"path": "app.py", "change_type": "modified", "plan_step_ids": ["step-1"]}],
        ),
        proposal,
    )
    assert "app.py" not in {item.path for item in request.files}

    checked = add_python_checks(request, root, proposal, None)

    bundled = {item.path: item for item in checked.files}
    originals = json.loads(base64.b64decode(bundled[".delta-code/originals.json"].content_base64))
    assert originals["app.py"] is not None


def test_no_baseline_means_no_test_check_and_no_approval(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    executor = ScriptedExecutor({})

    result = _service(
        tmp_path, RecordingModel(tmp_path, "client.send_v2(1)\n"), executor
    ).run(_context(root), root)

    assert [check.id for check in executor.requests[0].checks] == ["delta-code-imports"]
    assert result.evidence.recommendation.action == "revise"


def test_regression_earns_one_repair_with_the_failure_in_the_prompt(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    executor = ScriptedExecutor(REGRESSION, {})
    model = RecordingModel(tmp_path, "broken()\n", "client.send_v2(1)\n")

    result = _service(tmp_path, model, executor).run(_context(root), root)

    assert result.attempts == 2
    assert "no longer imports" in model.prompts[1].developer_instructions
    assert "no longer imports" not in (model.prompts[0].developer_instructions or "")
    assert result.evidence.verification_checks[0].status == "passed"


def test_second_regression_stops_and_cannot_be_approved(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    executor = ScriptedExecutor(REGRESSION, REGRESSION)
    model = RecordingModel(tmp_path, "broken()\n", "still_broken()\n")

    result = _service(tmp_path, model, executor).run(_context(root), root)

    assert result.attempts == 2
    assert len(model.prompts) == 2
    assert result.evidence.recommendation.action == "revise"


def test_failure_without_a_verdict_does_not_spend_on_a_repair(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    executor = ScriptedExecutor({"status": "failed", "exit_code": 3})
    model = RecordingModel(tmp_path, "client.send_v2(1)\n")

    result = _service(tmp_path, model, executor).run(_context(root), root)

    assert result.attempts == 1
    assert result.evidence.recommendation.action == "revise"


def test_text_that_does_not_line_up_earns_one_retry_and_then_fails(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    error = ReplacementError("replacement text was not found in app.py")

    model = RecordingModel(tmp_path, error, "client.send_v2(1)\n")
    result = _service(tmp_path, model, ScriptedExecutor({})).run(_context(root), root)
    assert result.attempts == 2
    assert "was not found in app.py" in model.prompts[1].developer_instructions

    model = RecordingModel(tmp_path, error, error)
    with pytest.raises(ReplacementError):
        _service(tmp_path, model, ScriptedExecutor()).run(_context(root), root)


def test_text_retry_does_not_use_up_the_regression_repair(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("client.send(1)\n")
    error = ReplacementError("replacement text matches 2 places in app.py")
    executor = ScriptedExecutor(REGRESSION, {})
    model = RecordingModel(tmp_path, error, "broken()\n", "client.send_v2(1)\n")

    result = _service(tmp_path, model, executor).run(_context(root), root)

    assert result.attempts == 3
    assert len(executor.requests) == 2
    assert result.evidence.verification_checks[0].status == "passed"


def test_review_sees_a_diff_rather_than_the_whole_file(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    padding = "".join(f"value_{index} = {index}\n" for index in range(200))
    (root / "app.py").write_text(f"client.send(1)\n{padding}")
    model = RecordingModel(tmp_path, f"client.send_v2(1)\n{padding}")

    _service(tmp_path, model, ScriptedExecutor({})).run(_context(root), root)

    reviewed = model.reviewed.files[0].content
    assert "-client.send(1)" in reviewed
    assert "+client.send_v2(1)" in reviewed
    assert "value_150" not in reviewed


def test_model_schema_asks_for_replacements_and_is_valid_for_strict_output():
    schema = strict_json_schema(ModelGenerationProposal.model_json_schema())

    edit = schema["$defs"]["ModelEdit"]
    assert set(edit["properties"]) == {"path", "replacements", "plan_step_ids"}
    assert schema["$defs"]["TextReplacement"]["required"] == [
        "old_text",
        "new_text",
        "replace_all",
    ]


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
