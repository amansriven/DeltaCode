<p align="center">
  <img src="docs/assets/brand/delta-code-mark.png" alt="Delta Code" width="88">
</p>

<h1 align="center">Delta Code</h1>

<p align="center">
  <strong>The AI review bot for breaking API changes.</strong>
</p>

<p align="center">
  Delta Code watches official API and SDK changes, finds the repositories they
  affect, writes and verifies the migration, then opens an evidence-rich draft
  pull request for a developer to review.
</p>

<p align="center">
  <a href="https://deltacode-tau.vercel.app/"><strong>Explore Delta Code</strong></a>
  ·
  <a href="docs/architecture/phase-0-rfc.md">Product RFC</a>
  ·
  <a href="docs/LOCAL_DEVELOPMENT.md">Run locally</a>
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-control%20plane-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="OpenAI" src="https://img.shields.io/badge/GPT--4o-migration%20intelligence-412991?style=flat-square&logo=openai&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-workflow-4169E1?style=flat-square&logo=postgresql&logoColor=white">
  <img alt="GitHub" src="https://img.shields.io/badge/GitHub-draft%20PRs-181717?style=flat-square&logo=github&logoColor=white">
</p>

---

## Dependabot finds version bumps. Delta Code ships API migrations.

External APIs change independently of the repositories that consume them.
Release notes, OpenAPI revisions, SDK releases, and migration guides arrive in
different formats and rarely identify the exact application code that must
change.

Delta Code closes that gap:

```text
Official change detected
        ↓
Affected repositories and call sites identified
        ↓
GPT-4o proposes a bounded migration and tests
        ↓
The exact patch is verified in an isolated sandbox
        ↓
GPT-4o reviews the patch against deterministic evidence
        ↓
An evidence-rich draft pull request is opened
        ↓
The developer approves, revises, snoozes, or declines
```

The product is deliberately review-native. It does not ask a developer to
start a generic coding-agent session, reconstruct a provider announcement, or
trust an unexplained patch. Every proposed change stays attached to its source,
affected call sites, verification checks, attempt history, and remaining
uncertainty.

## What the AI review bot does

GPT-4o is the migration intelligence layer. It receives bounded, repository-
specific context and produces schema-validated output for core migration jobs:

1. **Propose the migration.** Build a minimal plan, patch, test changes, and
   allowed verification commands from known provider evidence and call sites.
2. **Review the result.** Inspect the proposed patch and completed sandbox
   evidence, identify grounded findings, and recommend approve, revise,
   snooze, or decline.

The authenticated dashboard also exposes three explicit, user-controlled model
workflows: repository-scoped readiness and portfolio briefings, Ask Delta chat
over selected dashboard evidence, and per-pull-request overviews. Recent pull
requests are read from GitHub without invoking a model; a bounded diff, checks,
commits, and discussion are sent only after the developer clicks generate.

AI is never the source of truth for whether a check passed. Delta Code keeps
the authority boundary explicit:

| GPT-4o interprets and proposes | Deterministic systems establish |
| --- | --- |
| Provider prose and migration intent | Captured source artifacts and hashes |
| Repository-specific migration plans | Dependency and call-site evidence |
| Bounded code and test edits | Patch policy and file integrity |
| Evidence-grounded review findings | Build, lint, type-check, test, and behavior results |
| Developer-facing explanations | Git objects, draft PR state, and audit history |

Model requests use strict structured output, no tools, no browsing, bounded
input and output, `store: false`, retries with limits, and process-level cost
brakes. Repository and provider text is treated as untrusted data rather than
instructions.

## Product workflow

### 1. Watch authoritative sources

Collectors capture configured OpenAPI documents, structured releases,
changelogs, migration guides, and SDK releases. Artifacts are content-addressed
and normalized into a provider-independent change contract.

### 2. Trace the blast radius

Each change fans out across connected repositories. Delta Code inventories
dependencies, snapshots the selected commit, and locates supported call sites
with deterministic Python AST analysis and conservative JavaScript/TypeScript
evidence.

### 3. Generate a repository-specific fix

Only affected repositories receive migrations. GPT-4o works from bounded
provider evidence, immutable repository context, known call-site identifiers,
and developer revision instructions. Unknowns remain explicit instead of being
filled with guesses.

### 4. Verify before review

Structured edits pass patch policy in the trusted worker. Repository-controlled
commands run in a separate Cloudflare Sandbox boundary with outbound traffic
denied. The resulting logs, check statuses, resource cost, and artifact
references become immutable migration evidence.

### 5. Open the review where developers already work

Delta Code creates an owned branch, commits the exact verified patch, opens an
evidence-rich draft pull request, and publishes GitHub Checks. It never merges
automatically in the current product.

### 6. Keep the human decision explicit

The migration inbox supports approve, revise, snooze, decline, retry, and
publish actions. Regeneration creates a new immutable attempt rather than
overwriting history.

## The migration review experience

Each inbox item answers:

- Which provider changed, and what is the authoritative source?
- Which repository and call sites are affected?
- What did the AI review bot change, and why?
- Which files and tests are part of the patch?
- Which deterministic checks passed or failed?
- What uncertainty remains?
- Is the draft pull request ready for developer review?

The authenticated product includes:

- a migration review inbox with risk, deadline, status, and decision filters;
- provider-source health and synchronization coverage;
- normalized change details with before/after semantics and provenance;
- repository impact evidence and analysis limitations;
- AI-generated plans, patch intent, tests, and review recommendations;
- deterministic sandbox checks and immutable attempt history;
- draft-PR publishing and developer-controlled revision actions.

## Current capabilities

- Provider-neutral control-plane contracts and audited lifecycle transitions.
- Official-source ingestion, immutable captures, deduplication, and provenance.
- OpenAPI, structured JSON release, changelog, guide, and SDK source support.
- Repository snapshots, PyPI/npm dependency inventory, and impact fan-out.
- Python AST and conservative JavaScript/TypeScript call-site evidence.
- GPT-4o migration planning and completed-patch review through the Responses API.
- Strict JSON schemas, context limits, request limits, and model cost controls.
- Structured patch policy with content-hash checks and allowed command arrays.
- Fail-closed Cloudflare Sandbox execution and immutable verification evidence.
- Exact-patch GitHub commits, owned branches, draft PRs, and Check Runs.
- Migration inbox, provider operations, attempts, and developer decisions.
- Digest-cached GPT-4o workspace briefings with ranked priorities, portfolio
  risks, evidence links, and per-generation token and cost reporting.
- Labeled analyzer benchmarks, operational metrics, and security review gates.

## Security and trust boundary

Repository access is controlled by the GitHub App installation. Dashboard
identity uses a separate GitHub OAuth flow, and data is scoped to repositories
the signed-in user can access.

The migration path validates structured edits in the trusted worker and sends
repository-controlled commands to a separate sandbox executor. Hosted
execution remains fail-closed until explicitly enabled after isolation
configuration. Provider text, repository files, and developer notes are
untrusted inputs to the model; credentials and customer secrets are excluded
from prompts by default.

## Current scope

The active MVP is intentionally conservative:

- Python has the strongest semantic impact coverage.
- JavaScript/TypeScript analysis reports positive lexical evidence and exposes
  its limitations.
- Generated migrations are bounded to supplied files and known call sites.
- Sandbox verification requires an explicitly configured executor.
- Draft pull requests remain developer-controlled and are never auto-merged.
- Unsupported frameworks and incomplete source coverage become uncertainty,
  not confident guesses.

## Technology

| Area | Technology |
| --- | --- |
| API and control plane | FastAPI and Pydantic |
| Persistence and background work | PostgreSQL and Procrastinate |
| Migration intelligence | GPT-4o via the OpenAI Responses API |
| Sandboxed verification | Cloudflare Sandbox Worker |
| GitHub integration | GitHub Apps, OAuth, Checks, branches, and draft PRs |
| Dashboard | React 19, Next.js, TypeScript, and custom CSS |

## Documentation

- [Product RFC](docs/architecture/phase-0-rfc.md)
- [Architecture overview](docs/architecture/README.md)
- [Domain model](docs/architecture/domain-model.md)
- [Security and permissions](docs/architecture/security-and-permissions.md)
- [Source ingestion](docs/architecture/phase-2-ingestion.md)
- [Repository intelligence](docs/architecture/phase-3-repository-intelligence.md)
- [AI generation and sandbox verification](docs/architecture/phase-4-generation-and-sandbox.md)
- [GitHub publishing](docs/architecture/phase-5-github-publishing.md)
- [Migration inbox](docs/architecture/phase-6-migration-inbox.md)
- [Generalization and hardening](docs/architecture/phase-7-generalization-and-hardening.md)
- [Local development](docs/LOCAL_DEVELOPMENT.md)

---

<p align="center">
  <strong>Delta Code</strong><br>
  AI proposes. Evidence proves. You decide.
</p>
