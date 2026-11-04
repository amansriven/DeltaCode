from app.ingestion import tasks
from app.procrastinate_app import procrastinate_app


def _patch_sweep(monkeypatch, sources, *, queue=None, defer=None):
    failures = []
    monkeypatch.setattr(tasks, "list_enabled_sources", lambda: sources)
    monkeypatch.setattr(
        tasks,
        "queue_source_sync",
        queue or (lambda *_args, **_kwargs: (True, "queued")),
    )
    monkeypatch.setattr(tasks.sync_provider_source, "defer", defer or (lambda **_kwargs: None))
    monkeypatch.setattr(
        tasks,
        "mark_sync_status",
        lambda workspace_id, source_id, key, status, **kwargs: failures.append(
            (workspace_id, source_id, key, status, kwargs.get("error_code"))
        ),
    )
    return failures


def test_schedule_is_registered_as_an_hourly_periodic_task():
    registered = {
        periodic.task.name: periodic.cron
        for periodic in procrastinate_app.periodic_registry.periodic_tasks.values()
    }

    assert registered["schedule_source_syncs"] == "0 * * * *"


def test_sweep_queues_every_enabled_source_with_a_tick_scoped_key(monkeypatch):
    queued = []
    deferred = []
    _patch_sweep(
        monkeypatch,
        [("workspace-1", "openapi"), ("workspace-2", "changelog")],
        queue=lambda workspace_id, source_id, *, actor, idempotency_key: (
            queued.append((workspace_id, source_id, actor, idempotency_key)) or (True, "queued")
        ),
        defer=lambda **kwargs: deferred.append(kwargs),
    )

    tasks.schedule_source_syncs(timestamp=1800000000)

    assert queued == [
        ("workspace-1", "openapi", "system:scheduler", "scheduled-1800000000"),
        ("workspace-2", "changelog", "system:scheduler", "scheduled-1800000000"),
    ]
    assert [item["source_id"] for item in deferred] == ["openapi", "changelog"]
    assert {item["idempotency_key"] for item in deferred} == {"scheduled-1800000000"}


def test_repeated_tick_does_not_defer_a_second_sync(monkeypatch):
    deferred = []
    _patch_sweep(
        monkeypatch,
        [("workspace-1", "openapi")],
        queue=lambda *_args, **_kwargs: (False, "completed"),
        defer=lambda **kwargs: deferred.append(kwargs),
    )

    tasks.schedule_source_syncs(timestamp=1800000000)

    assert deferred == []


def test_queue_failure_marks_only_that_source_and_continues(monkeypatch):
    deferred = []

    def defer(**kwargs):
        if kwargs["source_id"] == "broken":
            raise RuntimeError("queue unavailable")
        deferred.append(kwargs["source_id"])

    failures = _patch_sweep(
        monkeypatch,
        [("workspace-1", "broken"), ("workspace-1", "healthy")],
        defer=defer,
    )

    tasks.schedule_source_syncs(timestamp=1800000000)

    assert deferred == ["healthy"]
    assert failures == [
        ("workspace-1", "broken", "scheduled-1800000000", "failed", "queue_failed")
    ]


def test_failure_before_a_request_exists_records_no_status(monkeypatch):
    def queue(_workspace_id, source_id, **_kwargs):
        if source_id == "broken":
            raise RuntimeError("database unavailable")
        return True, "queued"

    deferred = []
    failures = _patch_sweep(
        monkeypatch,
        [("workspace-1", "broken"), ("workspace-1", "healthy")],
        queue=queue,
        defer=lambda **kwargs: deferred.append(kwargs["source_id"]),
    )

    tasks.schedule_source_syncs(timestamp=1800000000)

    assert deferred == ["healthy"]
    assert failures == []
