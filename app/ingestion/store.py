"""PostgreSQL repository for ingestion metadata and atomic change fan-out."""

import json
from datetime import UTC, datetime, timedelta

from app.control_plane.models import NormalizedChange
from app.control_plane.store import (
    IdempotencyConflictError,
    NotFoundError,
    _decode_cursor,
    _encode_cursor,
)
from app.db import get_connection
from app.ingestion.models import (
    CapturedArtifact,
    CreateProviderRequest,
    CreateSourceRequest,
    IngestionResult,
    ProviderSource,
)


class ResourceConflictError(ValueError):
    pass


def _health_status(enabled: bool, failures: int, has_success: bool) -> str:
    if not enabled:
        return "disabled"
    if failures >= 3:
        return "failing"
    if failures:
        return "degraded"
    return "healthy" if has_success else "never_synced"


class PostgresIngestionRepository:
    def current_artifact(self, source: ProviderSource) -> CapturedArtifact | None:
        with get_connection() as conn:
            row = conn.execute(
                """SELECT a.data FROM provider_sources s
                   LEFT JOIN source_artifacts a
                     ON a.workspace_id = s.workspace_id AND a.id = s.current_artifact_id
                   WHERE s.workspace_id = %s AND s.id = %s""",
                (source.workspace_id, source.id),
            ).fetchone()
        return CapturedArtifact.model_validate(row[0]) if row and row[0] else None

    def commit(
        self,
        source: ProviderSource,
        artifact: CapturedArtifact,
        changes: list[NormalizedChange],
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> IngestionResult:
        operation = f"source-sync:{source.id}"
        with get_connection() as conn:
            prior = conn.execute(
                """SELECT request_hash, response FROM idempotency_records
                   WHERE workspace_id = %s AND operation = %s AND idempotency_key = %s""",
                (source.workspace_id, operation, idempotency_key),
            ).fetchone()
            if prior:
                if prior[0] != request_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for another source artifact"
                    )
                return IngestionResult.model_validate(prior[1])

            inserted_artifact = conn.execute(
                """INSERT INTO source_artifacts
                   (id, workspace_id, source_id, sha256, object_ref, data, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (workspace_id, source_id, sha256) DO NOTHING
                   RETURNING id""",
                (
                    artifact.id,
                    source.workspace_id,
                    source.id,
                    artifact.sha256,
                    artifact.object_ref,
                    json.dumps(artifact.model_dump(mode="json")),
                    artifact.captured_at + timedelta(days=source.retention_days),
                ),
            ).fetchone()
            now = datetime.now(UTC)
            event_ids = []
            fanout_count = 0
            for change in changes:
                event_data = {
                    **change.model_dump(mode="json"),
                    "version": 1,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                inserted_event = conn.execute(
                    """INSERT INTO change_events
                       (id, workspace_id, provider_id, dedupe_key, status, data)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (workspace_id, provider_id, dedupe_key) DO NOTHING
                       RETURNING id""",
                    (
                        change.id,
                        source.workspace_id,
                        source.provider.id,
                        change.dedupe_key,
                        change.status,
                        json.dumps(event_data),
                    ),
                ).fetchone()
                if inserted_event:
                    event_id = inserted_event[0]
                else:
                    event_id = conn.execute(
                        """SELECT id FROM change_events
                           WHERE workspace_id = %s AND provider_id = %s AND dedupe_key = %s""",
                        (source.workspace_id, source.provider.id, change.dedupe_key),
                    ).fetchone()[0]
                event_ids.append(event_id)
                for source_artifact in change.source_artifacts:
                    conn.execute(
                        """INSERT INTO change_evidence
                           (workspace_id, change_event_id, artifact_id, provenance)
                           VALUES (%s, %s, %s, 'authoritative_source')
                           ON CONFLICT DO NOTHING""",
                        (source.workspace_id, event_id, source_artifact.id),
                    )
                if inserted_event:
                    fanout_count += conn.execute(
                        """INSERT INTO change_fanout_jobs
                           (id, workspace_id, change_event_id, repository_id)
                           SELECT md5(%s || ':' || id), workspace_id, %s, id
                           FROM repositories
                           WHERE workspace_id = %s AND enabled = TRUE
                           ON CONFLICT DO NOTHING""",
                        (event_id, event_id, source.workspace_id),
                    ).rowcount

            source_data = _updated_source_data(source, artifact, now)
            conn.execute(
                """UPDATE provider_sources SET
                   current_artifact_id = %s, consecutive_failures = 0,
                   last_attempt_at = %s, last_success_at = %s, last_error_code = NULL,
                   etag = %s, last_modified = %s, status = 'healthy', data = %s,
                   updated_at = %s
                   WHERE workspace_id = %s AND id = %s""",
                (
                    artifact.id,
                    now,
                    now,
                    artifact.etag,
                    artifact.last_modified,
                    json.dumps(source_data),
                    now,
                    source.workspace_id,
                    source.id,
                ),
            )
            result = IngestionResult(
                source_id=source.id,
                artifact_id=artifact.id,
                artifact_created=bool(inserted_artifact),
                change_event_ids=event_ids,
                fanout_count=fanout_count,
            )
            conn.execute(
                """INSERT INTO idempotency_records
                   (workspace_id, operation, idempotency_key, request_hash, response)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    source.workspace_id,
                    operation,
                    idempotency_key,
                    request_hash,
                    json.dumps(result.model_dump(mode="json")),
                ),
            )
            _refresh_provider_health(conn, source.workspace_id, source.provider.id)
        return result

    def record_not_modified(
        self, source: ProviderSource, artifact: CapturedArtifact | None = None
    ) -> None:
        now = datetime.now(UTC)
        with get_connection() as conn:
            conn.execute(
                """UPDATE provider_sources SET status = 'healthy', consecutive_failures = 0,
                   last_attempt_at = %s, last_success_at = %s, last_error_code = NULL,
                   etag = COALESCE(%s, etag), last_modified = COALESCE(%s, last_modified),
                   data = data || jsonb_build_object(
                     'status', 'healthy', 'consecutive_failures', 0,
                     'last_attempt_at', %s::timestamptz, 'last_success_at', %s::timestamptz,
                     'last_error_code', NULL
                   ), updated_at = %s
                   WHERE workspace_id = %s AND id = %s""",
                (
                    now,
                    now,
                    artifact.etag if artifact else None,
                    artifact.last_modified if artifact else None,
                    now,
                    now,
                    now,
                    source.workspace_id,
                    source.id,
                ),
            )
            _refresh_provider_health(conn, source.workspace_id, source.provider.id)

    def record_failure(self, source: ProviderSource, code: str) -> None:
        now = datetime.now(UTC)
        with get_connection() as conn:
            row = conn.execute(
                """UPDATE provider_sources SET consecutive_failures = consecutive_failures + 1,
                   last_attempt_at = %s, last_error_code = %s, updated_at = %s
                   WHERE workspace_id = %s AND id = %s
                   RETURNING consecutive_failures, last_success_at, enabled""",
                (now, code, now, source.workspace_id, source.id),
            ).fetchone()
            if row:
                failures, last_success, enabled = row
                status = _health_status(enabled, failures, bool(last_success))
                conn.execute(
                    """UPDATE provider_sources SET status = %s,
                       data = data || jsonb_build_object(
                         'status', %s::text, 'consecutive_failures', %s::int,
                         'last_attempt_at', %s::timestamptz, 'last_error_code', %s::text
                       ) WHERE workspace_id = %s AND id = %s""",
                    (status, status, failures, now, code, source.workspace_id, source.id),
                )
                _refresh_provider_health(conn, source.workspace_id, source.provider.id)


def _updated_source_data(
    source: ProviderSource, artifact: CapturedArtifact, now: datetime
) -> dict:
    return {
        **source.model_dump(mode="json"),
        "status": "healthy",
        "consecutive_failures": 0,
        "last_attempt_at": now.isoformat(),
        "last_success_at": now.isoformat(),
        "last_error_code": None,
        "current_artifact_id": artifact.id,
        "updated_at": now.isoformat(),
    }


def _refresh_provider_health(conn, workspace_id: str, provider_id: str) -> None:
    rows = conn.execute(
        """SELECT status, last_success_at FROM provider_sources
           WHERE workspace_id = %s AND provider_id = %s AND enabled = TRUE""",
        (workspace_id, provider_id),
    ).fetchall()
    statuses = {row[0] for row in rows}
    successful_syncs = [row[1] for row in rows if row[1] is not None]
    last_synced_at = max(successful_syncs) if successful_syncs else None
    if statuses & {"degraded", "failing"}:
        status = "degraded"
    elif rows and statuses == {"healthy"}:
        status = "active"
    else:
        status = "disconnected"
    now = datetime.now(UTC)
    conn.execute(
        """UPDATE providers SET status = %s,
           data = data || jsonb_build_object(
             'status', %s::text, 'source_count', %s::int, 'last_synced_at', %s::timestamptz,
             'updated_at', %s::timestamptz
           ),
           updated_at = %s WHERE workspace_id = %s AND id = %s""",
        (status, status, len(rows), last_synced_at, now, now, workspace_id, provider_id),
    )


def load_source(workspace_id: str, source_id: str) -> ProviderSource:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT s.id, s.workspace_id, s.source_type, s.canonical_url,
               s.official_domains, s.adapter_id, s.enabled, s.max_artifact_bytes,
               s.retention_days, p.id, p.name, p.data->>'product'
               FROM provider_sources s
               JOIN providers p ON p.workspace_id = s.workspace_id AND p.id = s.provider_id
               WHERE s.workspace_id = %s AND s.id = %s""",
            (workspace_id, source_id),
        ).fetchone()
    if not row:
        raise NotFoundError(source_id)
    return ProviderSource(
        id=row[0],
        workspace_id=row[1],
        source_type=row[2],
        canonical_url=row[3],
        official_domains=row[4],
        adapter_id=row[5],
        enabled=row[6],
        max_artifact_bytes=row[7],
        retention_days=row[8],
        provider={"id": row[9], "name": row[10], "product": row[11]},
    )


def _request_hash(payload: dict) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _existing_idempotent_response(
    conn, workspace_id: str, operation: str, key: str, request_hash: str
) -> dict | None:
    prior = conn.execute(
        """SELECT request_hash, response FROM idempotency_records
           WHERE workspace_id = %s AND operation = %s AND idempotency_key = %s""",
        (workspace_id, operation, key),
    ).fetchone()
    if not prior:
        return None
    if prior[0] != request_hash:
        raise IdempotencyConflictError("idempotency key was already used with another request")
    return prior[1]


def create_provider(
    workspace_id: str,
    request: CreateProviderRequest,
    *,
    actor: str,
    idempotency_key: str,
) -> dict:
    operation = "provider:create"
    payload = request.model_dump(mode="json")
    request_hash = _request_hash(payload)
    now = datetime.now(UTC)
    response = {
        **payload,
        "status": "disconnected",
        "source_count": 0,
        "last_synced_at": None,
        "updated_at": now.isoformat(),
    }
    with get_connection() as conn:
        prior = _existing_idempotent_response(
            conn, workspace_id, operation, idempotency_key, request_hash
        )
        if prior:
            return prior
        inserted = conn.execute(
            """INSERT INTO providers (id, workspace_id, name, status, data)
               VALUES (%s, %s, %s, 'disconnected', %s)
               ON CONFLICT DO NOTHING RETURNING id""",
            (request.id, workspace_id, request.name, json.dumps(response)),
        ).fetchone()
        if not inserted:
            raise ResourceConflictError("provider already exists")
        _write_config_audit(
            conn, workspace_id, actor, "provider.created", "provider", request.id, now
        )
        conn.execute(
            """INSERT INTO idempotency_records
               (workspace_id, operation, idempotency_key, request_hash, response)
               VALUES (%s, %s, %s, %s, %s)""",
            (workspace_id, operation, idempotency_key, request_hash, json.dumps(response)),
        )
    return response


def create_source(
    workspace_id: str,
    provider_id: str,
    request: CreateSourceRequest,
    *,
    actor: str,
    idempotency_key: str,
) -> dict:
    operation = f"provider:{provider_id}:source:create"
    payload = request.model_dump(mode="json")
    request_hash = _request_hash(payload)
    now = datetime.now(UTC)
    response = {
        **payload,
        "provider_id": provider_id,
        "status": "never_synced",
        "enabled": True,
        "consecutive_failures": 0,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error_code": None,
        "current_artifact_id": None,
        "updated_at": now.isoformat(),
    }
    with get_connection() as conn:
        prior = _existing_idempotent_response(
            conn, workspace_id, operation, idempotency_key, request_hash
        )
        if prior:
            return prior
        provider = conn.execute(
            "SELECT 1 FROM providers WHERE workspace_id = %s AND id = %s",
            (workspace_id, provider_id),
        ).fetchone()
        if not provider:
            raise NotFoundError(provider_id)
        inserted = conn.execute(
            """INSERT INTO provider_sources
               (id, workspace_id, provider_id, source_type, canonical_url,
                official_domains, adapter_id, max_artifact_bytes, retention_days, data)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING id""",
            (
                request.id,
                workspace_id,
                provider_id,
                request.source_type,
                str(request.canonical_url),
                json.dumps(request.official_domains),
                request.adapter_id,
                request.max_artifact_bytes,
                request.retention_days,
                json.dumps(response),
            ),
        ).fetchone()
        if not inserted:
            raise ResourceConflictError("provider source already exists")
        conn.execute(
            """UPDATE providers SET data = data || jsonb_build_object(
                 'source_count', (SELECT count(*) FROM provider_sources
                    WHERE workspace_id = %s AND provider_id = %s),
                 'updated_at', %s::timestamptz
               ), updated_at = %s WHERE workspace_id = %s AND id = %s""",
            (workspace_id, provider_id, now, now, workspace_id, provider_id),
        )
        _write_config_audit(
            conn, workspace_id, actor, "provider_source.created", "provider_source", request.id, now
        )
        conn.execute(
            """INSERT INTO idempotency_records
               (workspace_id, operation, idempotency_key, request_hash, response)
               VALUES (%s, %s, %s, %s, %s)""",
            (workspace_id, operation, idempotency_key, request_hash, json.dumps(response)),
        )
    return response


def list_sources(
    workspace_id: str,
    provider_id: str,
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict], str | None]:
    position = _decode_cursor(cursor)
    where = "workspace_id = %s AND provider_id = %s"
    params: list = [workspace_id, provider_id]
    if position:
        where += " AND (created_at, id) < (%s, %s)"
        params.extend(position)
    params.append(limit + 1)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT id, data, created_at FROM provider_sources WHERE {where}
                ORDER BY created_at DESC, id DESC LIMIT %s""",  # noqa: S608
            params,
        ).fetchall()
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1][2], page[-1][0]) if len(rows) > limit else None
    return [row[1] for row in page], next_cursor


def list_enabled_sources() -> list[tuple[str, str]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT workspace_id, id FROM provider_sources
               WHERE enabled ORDER BY workspace_id, id"""
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def queue_source_sync(
    workspace_id: str,
    source_id: str,
    *,
    actor: str,
    idempotency_key: str,
) -> tuple[bool, str]:
    now = datetime.now(UTC)
    with get_connection() as conn:
        inserted = conn.execute(
            """INSERT INTO source_sync_requests
               (workspace_id, source_id, idempotency_key)
               VALUES (%s, %s, %s)
               ON CONFLICT DO NOTHING RETURNING status""",
            (workspace_id, source_id, idempotency_key),
        ).fetchone()
        if inserted:
            _write_config_audit(
                conn,
                workspace_id,
                actor,
                "provider_source.sync_requested",
                "provider_source",
                source_id,
                now,
            )
            return True, inserted[0]
        existing = conn.execute(
            """SELECT status FROM source_sync_requests
               WHERE workspace_id = %s AND source_id = %s AND idempotency_key = %s""",
            (workspace_id, source_id, idempotency_key),
        ).fetchone()
    return False, existing[0]


def mark_sync_status(
    workspace_id: str,
    source_id: str,
    idempotency_key: str,
    status: str,
    *,
    error_code: str | None = None,
    result: dict | None = None,
) -> None:
    if status not in {"running", "completed", "failed"}:
        raise ValueError("invalid source sync status")
    with get_connection() as conn:
        conn.execute(
            """UPDATE source_sync_requests SET status = %s, error_code = %s,
               result = %s, updated_at = now()
               WHERE workspace_id = %s AND source_id = %s AND idempotency_key = %s""",
            (
                status,
                error_code,
                json.dumps(result) if result is not None else None,
                workspace_id,
                source_id,
                idempotency_key,
            ),
        )


def _write_config_audit(
    conn,
    workspace_id: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    now: datetime,
) -> None:
    import uuid

    audit_id = str(uuid.uuid4())
    data = {
        "id": audit_id,
        "actor": actor,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": {},
        "created_at": now.isoformat(),
    }
    conn.execute(
        """INSERT INTO audit_events
           (id, workspace_id, actor, action, entity_type, entity_id, metadata, data)
           VALUES (%s, %s, %s, %s, %s, %s, '{}', %s)""",
        (audit_id, workspace_id, actor, action, entity_type, entity_id, json.dumps(data)),
    )
