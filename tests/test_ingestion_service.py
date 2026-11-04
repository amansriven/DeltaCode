import hashlib
import json
from datetime import UTC, datetime

import pytest

from app.control_plane.store import IdempotencyConflictError
from app.ingestion.adapters import DEFAULT_ADAPTERS
from app.ingestion.models import (
    ArtifactDescriptor,
    CapturedArtifact,
    IngestionResult,
    ProviderSource,
)
from app.ingestion.service import IngestionService
from app.ingestion.storage import FilesystemArtifactStore


class QueueCollector:
    def __init__(self, artifacts):
        self.artifacts = list(artifacts)

    def discover(self, configured, **_cache):
        return [
            ArtifactDescriptor(
                source_id=configured.id,
                canonical_url=configured.canonical_url,
                official_domains=configured.official_domains,
                max_artifact_bytes=configured.max_artifact_bytes,
                accepted_media_types=["application/json"],
            )
        ]

    def fetch(self, _descriptor):
        return self.artifacts.pop(0)


class MemoryRepository:
    def __init__(self, *, repository_count=2):
        self.artifact = None
        self.artifacts = set()
        self.events = {}
        self.idempotency = {}
        self.repository_count = repository_count
        self.failures = []
        self.not_modified = 0

    def current_artifact(self, _source):
        return self.artifact

    def commit(self, source, artifact, changes, *, idempotency_key, request_hash):
        prior = self.idempotency.get(idempotency_key)
        if prior:
            if prior[0] != request_hash:
                raise IdempotencyConflictError("key conflict")
            return prior[1]
        created_artifact = artifact.sha256 not in self.artifacts
        self.artifacts.add(artifact.sha256)
        self.artifact = artifact
        new_events = 0
        for change in changes:
            if change.dedupe_key not in self.events:
                self.events[change.dedupe_key] = change
                new_events += 1
        result = IngestionResult(
            source_id=source.id,
            artifact_id=artifact.id,
            artifact_created=created_artifact,
            change_event_ids=[change.id for change in changes],
            fanout_count=new_events * self.repository_count,
        )
        self.idempotency[idempotency_key] = (request_hash, result)
        return result

    def record_not_modified(self, _source, _artifact=None):
        self.not_modified += 1

    def record_failure(self, _source, code):
        self.failures.append(code)


def configured_source():
    return ProviderSource(
        id="releases",
        workspace_id="workspace-1",
        provider={"id": "example", "name": "Example"},
        source_type="structured_release",
        canonical_url="https://releases.example.com/changes.json",
        official_domains=["example.com"],
        adapter_id="structured-release.v1",
    )


def captured(store, identifier, content, minute):
    digest = hashlib.sha256(content).hexdigest()
    object_ref = store.put(content, digest)
    return CapturedArtifact(
        id=identifier,
        source_id="releases",
        canonical_url="https://releases.example.com/changes.json",
        retrieved_url="https://releases.example.com/changes.json",
        captured_at=datetime(2026, 8, 5, 12, minute, tzinfo=UTC),
        retrieval_status="captured",
        media_type="application/json",
        size_bytes=len(content),
        sha256=digest,
        object_ref=object_ref,
        collector_id="test",
        collector_version="1.0",
    )


def release_document(extra=""):
    return (
        json.dumps(
            {
                "changes": [
                    {
                        "key": "v5-remove-send",
                        "change_type": "sdk_symbol_removed",
                        "severity": "high",
                        "summary": "Client.send was removed.",
                        "targets": [{"kind": "symbol", "name": "Client.send"}],
                    }
                ]
            }
        )
        + extra
    ).encode()


def test_ingestion_deduplicates_repeated_feed_entries_and_fans_out_only_once(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    first = captured(store, "artifact-1", release_document(), 0)
    second = captured(store, "artifact-2", release_document("\n"), 1)
    repository = MemoryRepository(repository_count=3)
    service = IngestionService(
        QueueCollector([first, second]), store, repository, DEFAULT_ADAPTERS
    )

    first_result = service.sync(configured_source(), idempotency_key="sync-first")
    second_result = service.sync(configured_source(), idempotency_key="sync-second")

    assert first_result.fanout_count == 3
    assert second_result.fanout_count == 0
    assert len(repository.events) == 1
    assert repository.events["example:v5-remove-send"].claims[0].provenance == "provider_stated"


def test_ingestion_suppresses_identical_artifact_without_normalizing_again(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    artifact = captured(store, "artifact-1", release_document(), 0)
    repository = MemoryRepository()
    service = IngestionService(
        QueueCollector([artifact, artifact]), store, repository, DEFAULT_ADAPTERS
    )

    service.sync(configured_source(), idempotency_key="sync-first")
    result = service.sync(configured_source(), idempotency_key="sync-second")

    assert result.unchanged is True
    assert repository.not_modified == 1


def test_lost_baseline_starts_a_new_baseline_instead_of_failing_every_later_sync(tmp_path):
    root = tmp_path / "artifacts"
    store = FilesystemArtifactStore(root)
    first = captured(store, "artifact-1", release_document(), 0)
    second = captured(store, "artifact-2", release_document(" "), 1)
    third = captured(store, "artifact-3", release_document("  "), 2)
    repository = MemoryRepository()
    service = IngestionService(
        QueueCollector([first, second, third]), store, repository, DEFAULT_ADAPTERS
    )
    service.sync(configured_source(), idempotency_key="sync-first")

    (root / first.object_ref).unlink()
    recovered = service.sync(configured_source(), idempotency_key="sync-second")
    following = service.sync(configured_source(), idempotency_key="sync-third")

    assert recovered.artifact_id == "artifact-2"
    assert following.artifact_id == "artifact-3"
    assert repository.failures == []


def test_corrupted_baseline_still_fails_loudly(tmp_path):
    root = tmp_path / "artifacts"
    store = FilesystemArtifactStore(root)
    first = captured(store, "artifact-1", release_document(), 0)
    second = captured(store, "artifact-2", release_document(" "), 1)
    service = IngestionService(
        QueueCollector([first, second]), store, MemoryRepository(), DEFAULT_ADAPTERS
    )
    service.sync(configured_source(), idempotency_key="sync-first")

    path = root / first.object_ref
    path.chmod(0o600)
    path.write_bytes(b"tampered")

    with pytest.raises(ValueError):
        service.sync(configured_source(), idempotency_key="sync-second")


def test_repository_idempotency_conflict_is_not_misreported_as_source_failure(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    first = captured(store, "artifact-1", release_document(), 0)
    second = captured(store, "artifact-2", release_document("\n"), 1)
    repository = MemoryRepository()
    service = IngestionService(
        QueueCollector([first, second]), store, repository, DEFAULT_ADAPTERS
    )

    service.sync(configured_source(), idempotency_key="same-key")
    with pytest.raises(IdempotencyConflictError):
        service.sync(configured_source(), idempotency_key="same-key")

    assert repository.failures == []
