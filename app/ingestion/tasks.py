"""Durable Phase 2 source synchronization job."""

import logging
import os
from pathlib import Path

import httpx

from app.hardening.metrics import JobObservation
from app.ingestion.adapters import DEFAULT_ADAPTERS
from app.ingestion.collector import HTTPSCollector
from app.ingestion.service import IngestionService
from app.ingestion.storage import FilesystemArtifactStore
from app.ingestion.store import (
    PostgresIngestionRepository,
    list_enabled_sources,
    load_source,
    mark_sync_status,
    queue_source_sync,
)
from app.procrastinate_app import procrastinate_app

logger = logging.getLogger(__name__)

SCHEDULED_SYNC_ACTOR = "system:scheduler"


@procrastinate_app.task(name="sync_provider_source")
def sync_provider_source(workspace_id: str, source_id: str, idempotency_key: str) -> None:
    observation = JobObservation("ingestion")
    source = load_source(workspace_id, source_id)
    mark_sync_status(workspace_id, source_id, idempotency_key, "running")
    storage_root = Path(os.environ.get("ARTIFACT_STORAGE_ROOT", "/tmp/delta-code-artifacts"))
    artifact_store = FilesystemArtifactStore(
        storage_root, retention_days=source.retention_days
    )
    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={"User-Agent": "Delta-Code-Ingestion/1.0"},
        ) as client:
            collector = HTTPSCollector(client, artifact_store)
            service = IngestionService(
                collector,
                artifact_store,
                PostgresIngestionRepository(),
                DEFAULT_ADAPTERS,
            )
            result = service.sync(source, idempotency_key=idempotency_key)
    except Exception as exc:
        observation.finish("failed")
        mark_sync_status(
            workspace_id,
            source_id,
            idempotency_key,
            "failed",
            error_code=getattr(exc, "code", "sync_failed"),
        )
        raise
    mark_sync_status(
        workspace_id,
        source_id,
        idempotency_key,
        "completed",
        result=result.model_dump(mode="json"),
    )
    observation.finish("completed")
    from app.repository_intelligence.tasks import enqueue_repository_analysis

    enqueue_repository_analysis.defer(
        workspace_id=workspace_id,
        change_event_ids=result.change_event_ids,
    )


@procrastinate_app.periodic(cron="0 * * * *")
@procrastinate_app.task(name="schedule_source_syncs", queueing_lock="schedule_source_syncs")
def schedule_source_syncs(timestamp: int) -> None:
    # The cron timestamp makes the key stable, so a repeated tick cannot queue a second sync.
    idempotency_key = f"scheduled-{timestamp}"
    for workspace_id, source_id in list_enabled_sources():
        created = False
        try:
            created, _status = queue_source_sync(
                workspace_id,
                source_id,
                actor=SCHEDULED_SYNC_ACTOR,
                idempotency_key=idempotency_key,
            )
            if created:
                sync_provider_source.defer(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    idempotency_key=idempotency_key,
                )
        except Exception:
            # One broken source must not stop the rest of the hourly sweep.
            logger.exception("scheduled sync could not be queued for source %s", source_id)
            if created:
                mark_sync_status(
                    workspace_id,
                    source_id,
                    idempotency_key,
                    "failed",
                    error_code="queue_failed",
                )
