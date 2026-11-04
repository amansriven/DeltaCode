"""Idempotent source-to-change ingestion orchestration."""

import hashlib
import json
import logging
from typing import Protocol

from pydantic import ValidationError

from app.control_plane.models import NormalizedChange
from app.ingestion.adapters import AdapterError, SourceAdapter
from app.ingestion.collector import CollectionError, HTTPSCollector
from app.ingestion.models import CapturedArtifact, IngestionResult, ProviderSource
from app.ingestion.storage import ArtifactStore

logger = logging.getLogger(__name__)


class IngestionRepository(Protocol):
    def current_artifact(self, source: ProviderSource) -> CapturedArtifact | None: ...
    def commit(
        self,
        source: ProviderSource,
        artifact: CapturedArtifact,
        changes: list[NormalizedChange],
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> IngestionResult: ...
    def record_not_modified(
        self, source: ProviderSource, artifact: CapturedArtifact | None = None
    ) -> None: ...
    def record_failure(self, source: ProviderSource, code: str) -> None: ...


class IngestionService:
    def __init__(
        self,
        collector: HTTPSCollector,
        artifact_store: ArtifactStore,
        repository: IngestionRepository,
        adapters: dict[str, SourceAdapter],
    ):
        self.collector = collector
        self.artifact_store = artifact_store
        self.repository = repository
        self.adapters = adapters

    def sync(self, source: ProviderSource, *, idempotency_key: str) -> IngestionResult:
        if not source.enabled:
            raise ValueError("provider source is disabled")
        adapter = self.adapters.get(source.adapter_id)
        if adapter is None:
            raise ValueError(f"unknown source adapter: {source.adapter_id}")
        capabilities = adapter.capabilities()
        if source.source_type not in capabilities.source_types:
            raise ValueError("adapter does not support this source type")

        previous = self.repository.current_artifact(source)
        descriptor = self.collector.discover(
            source,
            etag=previous.etag if previous else None,
            last_modified=previous.last_modified if previous else None,
        )[0].model_copy(
            update={
                "max_artifact_bytes": min(
                    source.max_artifact_bytes, capabilities.maximum_artifact_bytes
                )
            }
        )
        try:
            captured = self.collector.fetch(descriptor)
            if captured.retrieval_status == "not_modified":
                if previous is None:
                    error = CollectionError(
                        "not_modified_without_baseline",
                        "source returned not-modified before any artifact was captured",
                    )
                    self.repository.record_failure(source, error.code)
                    raise error
                self.repository.record_not_modified(source, captured)
                return IngestionResult(
                    source_id=source.id,
                    artifact_id=previous.id if previous else None,
                    unchanged=True,
                )
            if previous and previous.sha256 == captured.sha256:
                self.repository.record_not_modified(source, captured)
                return IngestionResult(
                    source_id=source.id,
                    artifact_id=previous.id,
                    unchanged=True,
                )
        except CollectionError as exc:
            if exc.code != "not_modified_without_baseline":
                self.repository.record_failure(source, exc.code)
            raise

        previous_input = None
        if previous:
            try:
                previous_input = (previous, self.artifact_store.read(previous.object_ref))
            except FileNotFoundError:
                # Without its bytes no diff is possible, and failing here would repeat on every
                # later change. Start a new baseline; this one change cannot be detected.
                logger.warning(
                    "baseline artifact %s is missing for source %s; starting a new baseline",
                    previous.id,
                    source.id,
                )
        current_input = (captured, self.artifact_store.read(captured.object_ref))
        try:
            changes = adapter.normalize(source, previous_input, current_input)
        except (AdapterError, ValidationError):
            self.repository.record_failure(source, "normalization_failed")
            raise
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "source_id": source.id,
                    "artifact_sha256": captured.sha256,
                    "adapter_id": adapter.adapter_id,
                    "adapter_version": adapter.adapter_version,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return self.repository.commit(
            source,
            captured,
            changes,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
