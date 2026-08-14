# Phase 7: generalization and hardening

- **Status:** Implemented production-gate foundation
- **Completed:** 2026-08-06
- **Depends on:** Phases 1–6

Phase 7 closes the planned roadmap by making analyzer coverage explicit across
more repositories, adding another official-source family, enforcing per-attempt
resource budgets, and turning correctness and operations into repeatable release
gates.

## Generalized source and language coverage

`official-json-feed.v1` accepts versioned JSON published on configured official
changelog, migration-guide, or SDK-release sources. It supports the complete
normalized change vocabulary while preserving `provider_stated` provenance.
It does not parse arbitrary prose or upgrade provider claims to deterministic
facts.

Repository intelligence now uses `multilanguage-static:1.0.0`:

- Python retains semantic AST call, endpoint, and field evidence.
- JavaScript and TypeScript add exact, bounded lexical evidence for calls,
  endpoint strings, and object fields.
- PyPI and npm manifest/lockfile dependency evidence remains deterministic.
- An exact JavaScript/TypeScript match can prove `affected` with an explicit
  non-type-resolved limitation.
- A negative JavaScript/TypeScript scan remains `uncertain`; only complete
  Python AST coverage can currently produce `unaffected`.
- Other detected languages remain `unsupported` or contribute partial coverage.

This is deliberate positive-evidence support, not a claim of full JavaScript or
TypeScript semantic analysis.

## Labeled benchmark release gate

`benchmarks/repository-impact-v1.json` contains provider-neutral synthetic
positive, negative, dependency, and unsupported-language cases. The runner:

```bash
make benchmark
```

materializes each case in an isolated temporary directory, runs the real
inventory and analyzer, and reports precision, recall, F1, the confusion
matrix, and each classification. The default release threshold is 0.90
precision and 0.90 recall. `make test` includes the benchmark.

The initial dataset is intentionally small and synthetic. Its perfect score is
a regression signal, not an estimate of production accuracy. Beta evaluation
must grow this into provider- and repository-stratified, human-labeled cases.

## Attempt resource and cost controls

`GenerationLimits` enforces limits before model work, before sandbox execution,
and before accepting sandbox evidence:

| Environment variable | Default | Boundary |
| --- | ---: | --- |
| `GENERATION_MAX_CONTEXT_BYTES` | 1,500,000 | Serialized minimized planning context |
| `GENERATION_MAX_PROPOSAL_BYTES` | 2,500,000 | Structured plan and patch proposal |
| `GENERATION_MAX_CHECK_TIMEOUT_MS` | 600,000 | Aggregate requested check timeout |
| `GENERATION_MAX_SANDBOX_DURATION_MS` | 600,000 | Reported sandbox wall-clock duration |

Invalid configuration fails fast. A violation uses the safe durable error code
`cost_budget_exceeded`. Existing patch file/byte, command count, response size,
and sandbox request limits remain independently enforced.

Dollar-denominated model cost still belongs to the dedicated model gateway,
which owns provider pricing and token accounting. Delta Code stores reported
attempt cost when available; production enablement requires the gateway to
apply its own workspace quota before accepting a request.

## Observability and readiness

Every source synchronization, repository analysis, migration generation, and
GitHub publication records a fixed-cardinality outcome and duration. No
workspace, repository, migration, source, or error text appears as a metric
label.

`GET /metrics` exports Prometheus text only when
`METRICS_BEARER_TOKEN` is configured and a matching bearer credential is
provided. Scrape responses are `no-store`. Metrics are process-local; a hosted
deployment must scrape every web and worker replica into a central backend.

`GET /ready` returns `503` when PostgreSQL is unavailable and otherwise reports
the configuration state of artifact storage, sandbox execution, and GitHub
publishing without exposing paths, tokens, repository names, or exceptions.
`GET /health` remains a lightweight liveness check.

## Security review outcome

The code-level Phase 7 review passed the following gates:

- authoritative source domains, redirect policy, artifact size, JSON depth,
  and provenance remain enforced;
- repository acquisition uses short-lived installation credentials and exact
  immutable commits;
- repository analysis remains static and does not import packages or execute
  code;
- model context is minimized/redacted and structured output is validated;
- patches remain content-addressed, path-scoped, secret-scanned, and denied
  access to credentials and CI policy paths;
- sandbox execution and GitHub publishing retain independent fail-closed flags;
- sandbox success requires matching attempt/check identity, deny-all network,
  successful checks, and confirmed teardown;
- publication still commits the exact verified artifact, checks permissions,
  owns its branch, opens drafts, and never merges;
- state-changing web actions retain session authorization, same-origin checks,
  optimistic versions, and idempotency keys;
- metrics require a separate bearer credential and use bounded labels.

This is not a penetration-test or infrastructure-isolation certification. The
following residual risks remain release conditions:

1. Cloudflare Sandbox isolation, egress denial, image provenance, and teardown
   require controlled-environment adversarial validation before enabling an
   untrusted multi-tenant beta.
2. Artifact encryption and durable retention depend on hosted volume/object
   storage configuration.
3. JavaScript/TypeScript matches are positive-only lexical evidence.
4. Model dollar quotas and token pricing must be enforced and reported by the
   external gateway.
6. Process-local metrics require a central scraper and alert rules.

## Phase 7 exit criteria

1. Additional official-source types normalize through a provider-neutral
   adapter without changing provenance semantics.
2. JavaScript/TypeScript repositories can produce conservative positive impact
   evidence without false negative certification.
3. A labeled benchmark runs in the ordinary test gate with explicit thresholds.
4. Per-attempt context, proposal, check-timeout, and sandbox-duration budgets
   fail closed.
5. Durable worker outcomes and durations are observable with bounded labels.
6. Readiness distinguishes dependency failure from disabled optional features.
7. Security gates and residual release risks are explicitly documented.
