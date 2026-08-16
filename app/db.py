import os

import psycopg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://deltacode:deltacode@localhost:5432/deltacode"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    github_user_id BIGINT NOT NULL,
    github_login TEXT NOT NULL,
    github_name TEXT,
    avatar_url TEXT,
    accessible_repos JSONB NOT NULL DEFAULT '[]',
    repositories JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS github_name TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS repositories JSONB NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS oauth_login_states (
    state_hash TEXT PRIMARY KEY,
    redirect_to TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS oauth_login_states_expiry
ON oauth_login_states (expires_at);

CREATE TABLE IF NOT EXISTS oauth_completion_tickets (
    token_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    redirect_to TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS oauth_completion_tickets_expiry
ON oauth_completion_tickets (expires_at);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS providers (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS provider_sources (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    provider_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    official_domains JSONB NOT NULL,
    adapter_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    max_artifact_bytes INTEGER NOT NULL DEFAULT 5000000,
    retention_days INTEGER NOT NULL DEFAULT 90,
    status TEXT NOT NULL DEFAULT 'never_synced',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error_code TEXT,
    current_artifact_id TEXT,
    etag TEXT,
    last_modified TEXT,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, provider_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS source_artifacts (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_id TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    object_ref TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, source_id, sha256)
);

CREATE TABLE IF NOT EXISTS source_sync_requests (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    error_code TEXT,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, source_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS change_evidence (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    change_event_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    provenance TEXT NOT NULL,
    locator TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, change_event_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS change_fanout_jobs (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    change_event_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, change_event_id, repository_id)
);

CREATE TABLE IF NOT EXISTS repositories (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    full_name TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'unknown',
    default_branch TEXT NOT NULL DEFAULT 'main',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, full_name)
);

ALTER TABLE repositories ADD COLUMN IF NOT EXISTS clone_url TEXT;
ALTER TABLE repositories ADD COLUMN IF NOT EXISTS installation_id BIGINT;

CREATE TABLE IF NOT EXISTS change_events (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    provider_id TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, provider_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS impact_assessments (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    change_event_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    capability_report JSONB NOT NULL DEFAULT '{}',
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, change_event_id, repository_id, snapshot_digest)
);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    repository_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL CHECK (commit_sha ~ '^[a-fA-F0-9]{40}$'),
    content_digest TEXT NOT NULL,
    inventory_digest TEXT NOT NULL,
    inventory_version TEXT NOT NULL,
    inventory JSONB NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, repository_id, content_digest, inventory_digest)
);

CREATE TABLE IF NOT EXISTS repository_dependencies (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    snapshot_id TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    ecosystem TEXT NOT NULL,
    package TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, snapshot_id, dependency_id)
);

CREATE TABLE IF NOT EXISTS repository_call_sites (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    assessment_id TEXT NOT NULL,
    call_site_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    path TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, assessment_id, call_site_id)
);

ALTER TABLE change_fanout_jobs ADD COLUMN IF NOT EXISTS error_code TEXT;

CREATE TABLE IF NOT EXISTS migrations (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    change_event_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    current_attempt_id TEXT,
    snoozed_until TIMESTAMPTZ,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

CREATE UNIQUE INDEX IF NOT EXISTS migrations_one_active_per_change_repo
ON migrations (workspace_id, change_event_id, repository_id)
WHERE status NOT IN ('declined', 'completed');

CREATE TABLE IF NOT EXISTS migration_attempts (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    migration_id TEXT NOT NULL,
    number INTEGER NOT NULL CHECK (number > 0),
    previous_attempt_id TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, migration_id, number),
    UNIQUE (workspace_id, migration_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS migration_artifacts (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    attempt_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    object_ref TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, attempt_id, id),
    FOREIGN KEY (workspace_id, attempt_id)
        REFERENCES migration_attempts(workspace_id, id),
    UNIQUE (workspace_id, attempt_id, kind, sha256)
);

CREATE TABLE IF NOT EXISTS pull_request_records (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    migration_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    last_attempt_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    branch TEXT NOT NULL,
    base_sha TEXT NOT NULL CHECK (base_sha ~ '^[a-fA-F0-9]{40}$'),
    patch_sha256 TEXT NOT NULL CHECK (patch_sha256 ~ '^[a-f0-9]{64}$'),
    tree_sha TEXT,
    commit_sha TEXT,
    remote_head_sha TEXT,
    pull_number INTEGER,
    pull_node_id TEXT,
    pull_url TEXT,
    check_run_id BIGINT,
    error_code TEXT,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, migration_id)
);

CREATE TABLE IF NOT EXISTS developer_decisions (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    migration_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    target_version INTEGER NOT NULL,
    reason TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, operation, idempotency_key)
);

ALTER TABLE idempotency_records
ADD COLUMN IF NOT EXISTS request_hash TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS workspace_ai_briefs (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    migration_digest TEXT NOT NULL CHECK (migration_digest ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed')),
    input_migrations JSONB NOT NULL,
    data JSONB,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, migration_digest)
);

CREATE TABLE IF NOT EXISTS pull_request_ai_overviews (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    repository_full_name TEXT NOT NULL,
    pull_number INTEGER NOT NULL CHECK (pull_number > 0),
    installation_id BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed')),
    head_sha TEXT,
    pull_updated_at TIMESTAMPTZ,
    input_snapshot JSONB,
    data JSONB,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, repository_full_name, pull_number)
);

CREATE TABLE IF NOT EXISTS workspace_ai_chat_messages (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    status TEXT NOT NULL CHECK (status IN ('ready', 'queued', 'running', 'failed')),
    content TEXT,
    scope JSONB NOT NULL DEFAULT '{}',
    data JSONB,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS change_events_workspace_feed
ON change_events (workspace_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS provider_sources_workspace_feed
ON provider_sources (workspace_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS source_artifacts_retention
ON source_artifacts (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS source_sync_queue
ON source_sync_requests (status, created_at);
CREATE INDEX IF NOT EXISTS change_fanout_queue
ON change_fanout_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS repository_snapshots_feed
ON repository_snapshots (workspace_id, repository_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS repository_dependencies_package
ON repository_dependencies (workspace_id, ecosystem, package);
CREATE INDEX IF NOT EXISTS impact_assessments_change_feed
ON impact_assessments (workspace_id, change_event_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS migrations_workspace_feed
ON migrations (workspace_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS migration_artifacts_attempt
ON migration_artifacts (workspace_id, attempt_id, created_at DESC);
CREATE INDEX IF NOT EXISTS pull_request_records_status
ON pull_request_records (status, updated_at);
CREATE INDEX IF NOT EXISTS audit_events_workspace_feed
ON audit_events (workspace_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS workspace_ai_briefs_feed
ON workspace_ai_briefs (workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS pull_request_ai_overviews_feed
ON pull_request_ai_overviews (workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS workspace_ai_chat_thread_feed
ON workspace_ai_chat_messages (workspace_id, thread_id, created_at);
"""


def get_connection() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


def init_schema() -> None:
    with get_connection() as conn:
        conn.execute(SCHEMA)
