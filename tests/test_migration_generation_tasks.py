from types import SimpleNamespace

import pytest

from app.migration_generation import tasks
from app.repository_intelligence.models import RepositoryWorkspace
from app.repository_intelligence.workspace import workspace_fingerprint


def test_service_prefers_openai_when_api_key_is_configured(monkeypatch, tmp_path):
    intelligence = object()
    executor = object()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ARTIFACT_STORAGE_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(tasks, "OpenAIMigrationIntelligence", lambda: intelligence)
    monkeypatch.setattr(tasks, "CloudflareSandboxExecutor", lambda *_args, **_kwargs: executor)

    service = tasks._service()

    assert service.intelligence is intelligence
    assert service.executor is executor


def test_generation_checks_out_exact_snapshot_and_always_cleans_up(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("old()\n")
    digest, files, size, symlinks = workspace_fingerprint(tmp_path)
    snapshot = SimpleNamespace(commit_sha="a" * 40, content_digest=digest)
    repository = SimpleNamespace(id="repo-1", installation_id=17)
    attempt = SimpleNamespace(
        repository=repository,
        snapshot=snapshot,
        migration_id="migration-1",
        attempt_id="attempt-1",
        previous_attempt_id=None,
        developer_instructions=None,
        change=object(),
        impact=object(),
    )
    workspace = RepositoryWorkspace(
        repository_id="repo-1",
        root=str(tmp_path),
        commit_sha=snapshot.commit_sha,
        content_digest=digest,
        file_count=files,
        size_bytes=size,
        symlink_count=symlinks,
    )
    events = []

    class Provider:
        def __init__(self, _broker):
            pass

        def materialize(self, selected_repository, ref, credential_handle):
            events.append(("materialize", selected_repository.id, ref, credential_handle))
            return workspace

        def cleanup(self, selected_workspace):
            events.append(("cleanup", selected_workspace.root))

    class Service:
        def run(self, context, root):
            events.append(("run", context, root))
            return SimpleNamespace(evidence="evidence", patch_object_ref="object-ref")

    monkeypatch.setattr(tasks, "GitRepositoryWorkspaceProvider", Provider)
    monkeypatch.setattr(tasks, "GitHubInstallationCredentialBroker", lambda: object())
    monkeypatch.setattr(tasks, "claim_attempt", lambda *_args: attempt)
    monkeypatch.setattr(tasks, "assemble_planning_context", lambda **_kwargs: "context")
    monkeypatch.setattr(tasks, "_service", Service)
    monkeypatch.setattr(
        tasks,
        "complete_attempt",
        lambda context, evidence, object_ref: events.append(
            ("complete", context.attempt_id, evidence, object_ref)
        ),
    )

    tasks.run_migration_generation.func("workspace-1", "attempt-1")

    assert events == [
        ("materialize", "repo-1", "a" * 40, "github-installation:17"),
        ("run", "context", tmp_path),
        ("complete", "attempt-1", "evidence", "object-ref"),
        ("cleanup", str(tmp_path)),
    ]


def test_generation_failure_persists_only_exception_code(monkeypatch):
    failures = []
    repository = SimpleNamespace(id="repo-1", installation_id=17)
    attempt = SimpleNamespace(
        repository=repository,
        snapshot=SimpleNamespace(commit_sha="a" * 40),
    )

    class Provider:
        def __init__(self, _broker):
            pass

        def materialize(self, *_args):
            raise RuntimeError("secret-token must not be persisted")

    monkeypatch.setattr(tasks, "GitRepositoryWorkspaceProvider", Provider)
    monkeypatch.setattr(tasks, "GitHubInstallationCredentialBroker", lambda: object())
    monkeypatch.setattr(tasks, "claim_attempt", lambda *_args: attempt)
    monkeypatch.setattr(
        tasks,
        "fail_attempt",
        lambda workspace_id, attempt_id, code: failures.append(
            (workspace_id, attempt_id, code)
        ),
    )

    with pytest.raises(RuntimeError, match="secret-token"):
        tasks.run_migration_generation.func("workspace-1", "attempt-1")

    assert failures == [("workspace-1", "attempt-1", "runtimeerror")]
