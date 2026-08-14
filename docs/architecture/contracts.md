# System contracts

These contracts keep provider parsing, repository understanding, model work,
untrusted execution, deterministic verification, and GitHub writes isolated.
Examples are illustrative Python shapes rather than committed implementation
APIs. Phase 1 should express the accepted shapes as versioned Pydantic models.

## Contract rules

Every contract must be:

- Versioned independently from its implementation.
- Serializable for durable background jobs.
- Idempotent or supplied with an idempotency key.
- Explicit about deterministic, inferred, and human-supplied provenance.
- Forward-compatible through additive fields and capability negotiation.
- Validated at both producer and consumer boundaries.
- Safe to log after documented redaction.

Core orchestration may branch on declared capabilities or states. It may not
branch on provider names, SDK vendors, or programming languages.

## Source ingestion contract

Source ingestion performs network access and artifact capture. Provider
normalizers do not fetch arbitrary URLs themselves.

```python
class SourceCollector(Protocol):
    collector_version: str

    def discover(self, source: ProviderSource) -> list[ArtifactDescriptor]: ...
    def fetch(self, descriptor: ArtifactDescriptor) -> CapturedArtifact: ...
```

`CapturedArtifact` contains:

- Canonical and retrieved URL.
- Official-source classification and how it was established.
- Retrieval timestamp, status, media type, size, and SHA-256 digest.
- Immutable object reference.
- Cache validators such as ETag or last-modified where available.
- Collector version and redacted diagnostics.

Collectors enforce allowlisted hosts, response size limits, timeouts, content
type checks, redirect limits, and protection against private-network access.

## Provider adapter contract

Adapters convert captured official artifacts into provider-independent change
candidates.

```python
class ProviderAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def capabilities(self) -> AdapterCapabilities: ...
    def detect(
        self,
        previous: list[CapturedArtifact],
        current: list[CapturedArtifact],
    ) -> list[RawChange]: ...
    def normalize(
        self,
        raw_change: RawChange,
        artifacts: list[CapturedArtifact],
    ) -> NormalizedChangeCandidate: ...
```

The normalized output validates against
[`normalized-change.schema.json`](schemas/normalized-change.schema.json).

Adapters may use deterministic parsers and a model-backed interpretation step.
Any inferred claim is labeled and cites the artifacts from which it was
derived. The adapter cannot inspect repositories, generate patches, open pull
requests, or report verification success.

### Adapter capability manifest

The manifest declares:

- Source formats and schema versions.
- Change categories that can be detected deterministically.
- Change categories that require interpretation.
- Version and effective-date semantics.
- Known blind spots.
- Maximum artifact size and required source relationships.

This allows the platform to show source coverage rather than presenting every
provider as equally understood.

## Repository workspace contract

Repository acquisition resolves a GitHub repository and immutable commit into
an ephemeral workspace without leaking the installation token.

```python
class RepositoryWorkspaceProvider(Protocol):
    def materialize(
        self,
        repository: RepositoryRef,
        commit_sha: str,
        credential_handle: str,
    ) -> RepositoryWorkspace: ...
```

The output contains a workspace handle, commit SHA, repository metadata, size,
and content digest. It never contains a reusable GitHub token. Credentials are
resolved by a token broker and may only be used during acquisition or external
publication.

## Repository inventory contract

Inventory is deterministic and change-independent. It discovers languages,
manifests, resolved dependencies, generated clients, candidate provider
configuration, and supported analysis capabilities.

```python
class RepositoryInventory(Protocol):
    inventory_version: str

    def scan(self, workspace: RepositoryWorkspace) -> InventoryResult: ...
```

`InventoryResult` includes:

- Commit and workspace digests.
- Language and framework detections with evidence.
- Manifest and lockfile observations.
- Dependencies and versions with detection method.
- Provider associations with confidence and supporting files.
- Analyzer capability and coverage report.
- Redacted warnings and unsupported areas.

## Repository analyzer contract

Analyzers map normalized targets to concrete call sites and impact evidence.

```python
class RepositoryAnalyzer(Protocol):
    analyzer_id: str
    analyzer_version: str

    def capabilities(self) -> AnalyzerCapabilities: ...
    def assess(
        self,
        change: NormalizedChange,
        snapshot: RepositorySnapshot,
        inventory: InventoryResult,
    ) -> ImpactResult: ...
```

`ImpactResult` contains:

- Conclusion: affected, unaffected, uncertain, unsupported, or failed.
- Dependency matches and version reasoning.
- File, line range, symbol, endpoint, and configuration call sites.
- Detection method: lockfile, AST, type index, symbol index, generated-code
  marker, configuration, text heuristic, or model inference.
- Confidence and unresolved ambiguity.
- Coverage: files considered, files excluded, languages supported, parse
  failures, and generated/vendor directories skipped.

Only supported deterministic coverage may produce `unaffected` automatically.
Text search or model inference alone may produce `affected` or `uncertain`, but
not a high-confidence negative conclusion.

## Migration intelligence contract

Migration intelligence is the provider-neutral boundary behind which hosted or
local models may be used.

```python
class MigrationIntelligence(Protocol):
    def interpret_change(self, request: InterpretationRequest) -> InterpretationResult: ...
    def plan_migration(self, request: PlanningRequest) -> MigrationPlan: ...
    def generate_patch(self, request: GenerationRequest) -> PatchProposal: ...
    def review_attempt(self, request: ReviewRequest) -> ReviewResult: ...
```

All requests use bounded context assembled by deterministic code. The model
does not receive a repository token, provider credential, arbitrary environment
variables, unrelated repository content, or direct GitHub write access.

### Migration plan

A plan identifies:

- Change event and repository snapshot.
- Affected call-site ids.
- Ordered edits with expected files and intent.
- Tests to add or modify.
- Verification commands requested from declared repository capabilities.
- Expected behavior and rollback approach.
- Assumptions, uncertainty, and unsupported work.

### Patch proposal

A proposal contains a unified diff or structured file edits, new-test metadata,
plan-step mapping, explanation, and uncertainty. It cannot claim any command
passed. Paths are normalized and validated before sandbox application; edits
outside the materialized repository or forbidden policy paths are rejected.

### Review result

Review consumes the stored patch plus deterministic evidence. It returns
findings, confidence, unresolved uncertainty, and one recommendation:
approve, revise, snooze, or decline. It may not alter evidence or publish the
PR.

## Sandbox execution contract

The sandbox is the only component permitted to execute repository-controlled
code.

```python
class SandboxExecutor(Protocol):
    executor_id: str
    executor_version: str

    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
```

### Execution request

- Immutable base snapshot digest.
- Validated patch artifact digest.
- Toolchain image or capability selection.
- Ordered commands grouped by check kind.
- CPU, memory, disk, process, output, and wall-clock limits.
- Network policy and explicit host allowlist.
- Read-only public dependency credentials represented by opaque handles only
  when workspace policy permits them.
- Redaction rules and artifact-retention policy.

The orchestrator never accepts free-form model commands without policy
validation. Repository-provided scripts may be invoked only through declared
build-system commands and sandbox policy.

### Execution result

- Sandbox and image identifiers.
- Applied-patch digest and resulting worktree digest.
- One structured result per command: status, exit code, timing, truncated
  display output, full log artifact, and resource use.
- Generated files or patch delta.
- Behavioral evidence artifacts.
- Timeout, policy violation, or infrastructure failure classification.
- Redaction report.

An infrastructure failure is not a test failure. A skipped check is not a
passing check. These distinctions survive into the dashboard and PR.

## Verification contract

Verification converts execution results into policy evaluation without using a
model.

```python
class VerificationPolicy(Protocol):
    def evaluate(
        self,
        checks: list[VerificationCheck],
        repository_policy: RepositoryPolicy,
    ) -> VerificationDecision: ...
```

Check kinds include:

- dependency installation
- formatting
- lint
- type-check
- build
- unit test
- generated migration test
- repository-defined integration tests
- security/policy

Each check is `passed`, `failed`, `skipped`, `timed_out`, `blocked`, or
`infrastructure_error`. Repository policy determines required checks and
whether a draft may be published with failures.

## Evidence assembly contract

Evidence assembly combines source provenance, impact findings, plan, patch,
tests, checks, behavioral observations, and model review into the schema in
[`migration-evidence.schema.json`](schemas/migration-evidence.schema.json).

The assembler is deterministic. It labels every section with provenance and
rejects:

- Source claims without artifact ids.
- Call sites outside the recorded snapshot.
- Check results without executor provenance.
- Recommendation claims that contradict stored check status.
- Unknown file changes not represented by the stored patch digest.

## GitHub publisher contract

The publisher is a narrow trusted service. It receives a completed attempt and
validated evidence document, then performs Git operations using a short-lived
installation token.

```python
class PullRequestPublisher(Protocol):
    def open_draft(self, request: PublishRequest) -> PullRequestRecord: ...
    def update_draft(self, request: UpdateRequest) -> PullRequestRecord: ...
    def mark_ready(self, request: MarkReadyRequest) -> PullRequestRecord: ...
    def close(self, request: CloseRequest) -> PullRequestRecord: ...
```

The publisher:

- Verifies repository, base SHA, patch digest, attempt state, and workspace
  policy.
- Creates a Delta Code-owned branch with a collision-resistant name.
- Commits exactly the validated patch; it cannot generate additional edits.
- Renders the PR body from structured evidence.
- Opens drafts by default.
- Records remote ids and head SHA before reporting success.
- Is idempotent for migration plus attempt.
- Emits an audit event for every external write.

## Developer action contract

Actions require an authenticated workspace actor, target migration version,
reason where applicable, and idempotency key.

- Approve marks an eligible draft ready; it never merges.
- Revise creates a new attempt and preserves bounded developer instructions.
- Snooze records a wake condition and pauses automatic attempts.
- Decline terminates automatic work and optionally closes the generated draft.
- Reopen is explicit and audited.

Concurrent or stale actions fail with a version conflict rather than silently
overwriting a newer decision.

## Orchestration contract

The orchestrator coordinates durable state transitions and schedules work. It
does not parse providers, analyze language syntax, generate code, execute
repositories, or write to GitHub itself.

Every job carries:

- Workspace and entity ids.
- Expected entity state/version.
- Contract and implementation versions.
- Idempotency key.
- Attempt number where applicable.
- Trace id and causation id.

Retries are safe, terminal state is not overwritten, and poison jobs become a
visible blocked/failed state with redacted diagnostics.

## Planned control-plane API

The exact HTTP representation belongs to Phase 1, but the resource boundary is:

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

List endpoints are cursor-paginated and workspace-scoped. Mutations require
CSRF protection or an equivalent same-origin token in addition to the secure
session cookie.

## Contract testing

Phase 1 must include:

- JSON Schema fixtures for valid and invalid change/evidence documents.
- Two provider adapters using structurally different fixture sources.
- Analyzer fixtures for affected, unaffected, uncertain, and unsupported.
- Sandbox fake covering pass, test failure, timeout, policy violation, and
  infrastructure failure.
- Publisher fake proving idempotency and exact-patch publication.
- State-machine tests rejecting invalid and stale transitions.
- Redaction tests proving tokens and configured secret patterns cannot enter
  logs, evidence, or prompts.
