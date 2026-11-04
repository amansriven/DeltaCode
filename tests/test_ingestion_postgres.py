"""Ingestion SQL against a real server; psycopg parameter typing cannot be checked with fakes.

Runs only when TEST_DATABASE_URL points at a disposable database (see `make test-postgres`).
"""

import os
import uuid

import psycopg
import pytest

from app import db
from app.ingestion import store
from app.ingestion.models import CreateProviderRequest, CreateSourceRequest

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not set")


@pytest.fixture
def workspace(monkeypatch) -> str:
    monkeypatch.setattr(db, "DATABASE_URL", DATABASE_URL)
    db.init_schema()
    workspace_id = f"workspace-{uuid.uuid4().hex}"
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("INSERT INTO workspaces (id, name) VALUES (%s, %s)", (workspace_id, "test"))
    store.create_provider(
        workspace_id,
        CreateProviderRequest(id="example", name="Example"),
        actor="test",
        idempotency_key="provider-1",
    )
    return workspace_id


def _add_source(workspace_id: str, source_id: str) -> None:
    store.create_source(
        workspace_id,
        "example",
        CreateSourceRequest(
            id=source_id,
            source_type="openapi",
            canonical_url=f"https://api.example.com/{source_id}.json",
            official_domains=["api.example.com"],
            adapter_id="openapi.diff",
        ),
        actor="test",
        idempotency_key=f"source-{source_id}",
    )


def test_recording_a_failure_updates_source_and_provider_health(workspace):
    _add_source(workspace, "openapi")
    source = store.load_source(workspace, "openapi")

    store.PostgresIngestionRepository().record_failure(source, "dns_failed")

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            "SELECT consecutive_failures, last_error_code, data FROM provider_sources "
            "WHERE workspace_id = %s AND id = %s",
            (workspace, "openapi"),
        ).fetchone()
        provider = conn.execute(
            "SELECT data FROM providers WHERE workspace_id = %s AND id = %s",
            (workspace, "example"),
        ).fetchone()
    assert row[0] == 1
    assert row[1] == "dns_failed"
    assert row[2]["last_error_code"] == "dns_failed"
    assert row[2]["consecutive_failures"] == 1
    assert provider[0]["source_count"] == 1


def test_recording_success_without_change_refreshes_provider_health(workspace):
    _add_source(workspace, "openapi")
    source = store.load_source(workspace, "openapi")

    store.PostgresIngestionRepository().record_not_modified(source, None)

    with psycopg.connect(DATABASE_URL) as conn:
        provider = conn.execute(
            "SELECT status, data FROM providers WHERE workspace_id = %s AND id = %s",
            (workspace, "example"),
        ).fetchone()
    assert provider[0] == "active"
    assert provider[1]["status"] == "active"
    assert provider[1]["last_synced_at"] is not None


def test_only_enabled_sources_are_listed_and_tick_keys_deduplicate(workspace):
    _add_source(workspace, "enabled")
    _add_source(workspace, "disabled")
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "UPDATE provider_sources SET enabled = FALSE WHERE workspace_id = %s AND id = %s",
            (workspace, "disabled"),
        )

    listed = [item for item in store.list_enabled_sources() if item[0] == workspace]
    first = store.queue_source_sync(
        workspace, "enabled", actor="system:scheduler", idempotency_key="scheduled-1"
    )
    repeated = store.queue_source_sync(
        workspace, "enabled", actor="system:scheduler", idempotency_key="scheduled-1"
    )

    assert listed == [(workspace, "enabled")]
    assert first == (True, "queued")
    assert repeated == (False, "queued")
