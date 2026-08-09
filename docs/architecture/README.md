# Delta Code architecture

This directory defines the product and technical contracts for Delta Code, the
AI review bot for breaking API changes. The platform follows a Dependabot-style
workflow: authoritative provider change, repository impact assessment, bounded
GPT-4o migration generation and review, deterministic sandbox verification,
evidence-rich draft pull request, and explicit developer decision.

Phase 0 established the contracts. Phases 1–7 now implement the
provider-neutral control plane, official-source ingestion, and deterministic
repository intelligence, plus guarded generation and sandbox verification,
GitHub publishing, and the migration review experience while preserving the
legacy verification workflow.

The current OpenAI runtime uses GPT-4o through strict, tool-free Responses API
structured output. The same bounded client powers migration proposals,
completed-patch review, semantic behavior cases, finding explanations, and a
user-triggered dashboard triage brief. Model interpretation remains separate
from source provenance, call-site evidence, patch validation, sandbox results,
and Git state.

## Phase 0 documents

- [Product RFC](phase-0-rfc.md) — product boundary, experience, architecture,
  scope, success measures, and delivery gates.
- [Domain model](domain-model.md) — durable entities, ownership rules, and
  lifecycle state machines.
- [System contracts](contracts.md) — provider adapters, repository analyzers,
  migration intelligence, sandbox execution, verification, and publishing.
- [Security and permissions](security-and-permissions.md) — trust boundaries,
  GitHub permissions, token handling, sandbox policy, and LLM data controls.
- [Normalized change schema](schemas/normalized-change.schema.json) — the
  provider-independent output of change ingestion.
- [Migration evidence schema](schemas/migration-evidence.schema.json) — the
  repository-specific evidence used by the dashboard and draft PRs.
- [Example normalized change](examples/normalized-change.example.json) and
  [example migration evidence](examples/migration-evidence.example.json) — a
  fixture vertical slice used to validate both contracts.

## Phase 1 implementation

- [Control-plane implementation note](phase-1-control-plane.md) — persistence,
  HTTP resources, state transitions, idempotency, audit behavior, and the
  boundary handed to Phase 2 ingestion.
- `app/control_plane/models.py` — versioned Pydantic contracts.
- `app/control_plane/state.py` — explicit optimistic lifecycle transitions.
- `app/control_plane/store.py` and `router.py` — workspace-scoped PostgreSQL
  persistence and authenticated APIs.

## Phase 2 implementation

- [Official-source ingestion](phase-2-ingestion.md) — collectors, immutable
  captures, normalization, provenance, source health, and repository fan-out.
- [Artifact storage decision](decisions/0001-artifact-storage.md) — the initial
  content-addressed backend and retention policy.
- `app/ingestion/` — the source contracts, security policy, storage backend,
  adapters, orchestration service, durable task, API, and PostgreSQL repository.

## Phase 3 implementation

- [Repository intelligence](phase-3-repository-intelligence.md) — immutable
  workspaces and snapshots, dependency inventory, Python AST call sites,
  explicit coverage outcomes, durable fan-out, and affected migrations.
- `app/repository_intelligence/` — safe Git workspace acquisition,
  deterministic inventory and analyzer services, worker tasks, persistence,
  and authenticated read APIs.

## Phase 4 implementation

- [Migration generation and sandbox verification](phase-4-generation-and-sandbox.md)
  — bounded model context, structured patch policy, immutable attempt evidence,
  Cloudflare Sandbox execution, security gates, and operations.
- `app/migration_generation/` — generation contracts, context assembly, patch
  policy, model/executor boundaries, durable orchestration, and authenticated API.
- `sandbox-worker/` — the independently deployable, deny-by-default command
  execution boundary.

## Phase 5 implementation

- [GitHub publishing](phase-5-github-publishing.md) — exact-patch Git objects,
  owned branches, evidence-rich draft PRs, Check Runs, revision synchronization,
  decision synchronization, permission verification, and the write gate.
- `app/github_publishing/` — publisher contracts, artifact reconstruction,
  GitHub API boundary, durable checkpoints, orchestration, and authenticated API.

## Phase 6 implementation

- [Migration inbox](phase-6-migration-inbox.md) — authenticated information
  architecture, live progress, evidence and attempt review, developer actions,
  preview fixtures, accessibility, and responsive behavior.
- `frontend/app/MigrationWorkspace.tsx` — inbox, migration detail, normalized
  change detail, provider overview, and secured action forms.
- `frontend/app/lib/migrations.ts` — typed Phase 1–5 API client and explicitly
  labeled preview data.

## Phase 7 implementation

- [Generalization and hardening](phase-7-generalization-and-hardening.md) —
  official JSON feed support, conservative JavaScript/TypeScript analysis,
  labeled benchmarks, resource budgets, operational telemetry, readiness, and
  security review.
- `app/hardening/` — generation limits, fixed-cardinality metrics, protected
  operational endpoints, and the benchmark runner.
- `benchmarks/repository-impact-v1.json` — versioned analyzer release-gate data.

## Decision status

The product direction and architectural boundaries are accepted. Provider
selection and initial language coverage remain explicit implementation
decisions. Cloudflare Sandbox is the selected Phase 4 execution boundary. No
provider-specific decision may weaken the common contracts defined here.

## Phase 0 exit criteria

Phase 0 is complete when:

1. The product boundary and non-goals are agreed upon.
2. Change, impact, migration, attempt, evidence, and decision lifecycles are
   unambiguous.
3. Provider and repository integrations depend on common contracts.
4. Deterministic evidence is distinguishable from model interpretation.
5. GitHub write permissions and sandbox risks are documented before enablement.
6. Both JSON Schemas parse successfully and cover the first vertical slice.
7. Remaining implementation choices are recorded as decisions rather than
   hidden assumptions.
