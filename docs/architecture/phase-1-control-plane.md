# Phase 1: migration control plane

- **Status:** Implemented foundation
- **Contract version:** `1.0`
- **Depends on:** [Phase 0 RFC](phase-0-rfc.md), [domain model](domain-model.md),
  and [system contracts](contracts.md)

## Delivered boundary

Phase 1 introduces the provider-independent control plane. It does not ingest
live provider sources or execute repository migrations; those remain Phase 2
and Phase 4 concerns.

The implementation provides:

- versioned Pydantic models for normalized changes, API resources, developer
  actions, confidence/provenance, and durable orchestration job envelopes;
- workspace-scoped PostgreSQL records for providers, repositories, change
  events, impact assessments, migrations, immutable attempts, append-only
  decisions, audit events, and idempotency responses;
- explicit change, impact, migration, and attempt state machines with
  optimistic version checks;
- opaque cursor pagination for all planned control-plane feeds;
- authenticated change, migration, attempt, provider, repository, and audit
  read APIs;
- exact approve, revise, snooze, and decline endpoints with trusted-origin
  checks, idempotency keys, stale-write rejection, and audit records;
- revision behavior that creates a new attempt linked to its predecessor rather
  than overwriting evidence;
- structurally different OpenAPI and SDK-release fixture adapters that converge
  on one normalized change contract; and
- valid/invalid JSON Schema fixtures plus API, contract, and state-machine
  tests.

## HTTP resources

```text
GET  /changes
GET  /changes/{change_id}
GET  /migrations
GET  /migrations/{migration_id}
GET  /migrations/{migration_id}/attempts/{attempt_id}
POST /migrations/{migration_id}/approve
POST /migrations/{migration_id}/revise
POST /migrations/{migration_id}/snooze
POST /migrations/{migration_id}/decline
GET  /providers
GET  /repositories
GET  /audit-events
```

All resources derive their workspace from the authenticated GitHub session.
List responses have the shape `{ "items": [...], "next_cursor": "..." }`.

Mutations require:

- the secure authenticated session cookie;
- an `Origin` matching the configured frontend origin;
- an `Idempotency-Key` header of 8–200 characters; and
- `expected_version` in the request body.

Reusing a key with the same request returns the stored response. Reusing it for
a different request or acting on a stale entity version returns HTTP 409.

## Persistence and compatibility

The application startup schema remains additive. Repository access visible in
a GitHub session is synchronized into that user's control-plane workspace when
a control-plane endpoint is first accessed.

Large immutable source bodies, patches, and logs are intentionally absent from
these tables; later phases store only their object references in control-plane
records.

## Phase 2 handoff

Ingestion can now depend on `NormalizedChange` and persist ready events without
introducing provider branches into the API or migration lifecycle. Phase 2 must
add collectors, immutable artifact storage, production adapters, provenance
health, and idempotent event fan-out. It must not fetch arbitrary URLs from an
adapter or weaken the authoritative-source requirement.
