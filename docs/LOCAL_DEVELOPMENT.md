# Delta Code development and hosting runbook

The root `Makefile` is the shortest path for normal development. Run `make
help` at any time to see the available commands.

## Prerequisites

- Python 3.12 or newer
- Node.js 22.13 or newer
- Docker with Compose
- Git
- GNU Make or the Make version included with macOS

Railway CLI and the Vercel CLI are only required for command-line deployment.

## First-time setup

```bash
make setup
make db-up
make db-schema
```

`make setup` creates `.venv`, installs the backend in editable mode with test
dependencies, runs `npm ci` in `frontend/` and `sandbox-worker/`, and creates
`frontend/.env` if it does not exist. The default frontend API is the live
Railway service:

```text
NEXT_PUBLIC_DELTA_CODE_API_URL=https://web-production-e59907.up.railway.app
```

Override it without editing the Makefile:

```bash
make frontend-dev LIVE_API_URL=http://localhost:8000
```

## Run the application locally

Use three terminals after PostgreSQL is running.

Terminal 1 — API:

```bash
make api
```

Terminal 2 — comparison worker:

```bash
make worker
```

The same worker also runs official-source synchronization, repository
intelligence, and migration-generation jobs. Repository analysis checks out the
GitHub default branch ephemerally. Generation checks out the exact stored
snapshot commit, validates its digest, and removes the checkout after the job.

Terminal 3 — dashboard:

```bash
make frontend-dev
```

The services are available at:

| Service | URL |
| --- | --- |
| Dashboard | `http://localhost:3000` |
| API | `http://localhost:8000` |
| API health | `http://localhost:8000/health` |
| PostgreSQL | `postgresql://deltacode:deltacode@localhost:5432/deltacode` |

The API and worker read configuration from environment variables. Export
GitHub App and OAuth values in the shell before starting them when testing
webhooks, Check Runs, or the complete sign-in flow:

`make api` and `make worker` also load an ignored root `.env.local` when it
exists. Keep model credentials there for local development only; never put
them in `frontend/.env*` or in a `NEXT_PUBLIC_*` variable.

```bash
export GITHUB_APP_ID="..."
export GITHUB_PRIVATE_KEY="..."
export GITHUB_OAUTH_CLIENT_ID="..."
export GITHUB_OAUTH_CLIENT_SECRET="..."
export GITHUB_OAUTH_CALLBACK_URL="http://localhost:8000/auth/github/callback"
export FRONTEND_URL="http://localhost:3000"
export ARTIFACT_STORAGE_ROOT="$PWD/.delta-code-artifacts"
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o"
# Process-local safety brakes. Also configure a hard project budget at OpenAI.
export LLM_DAILY_BUDGET_USD="1.00"
export LLM_TOTAL_BUDGET_USD="9.00"
export LLM_MAX_REQUEST_COST_USD="0.20"
export LLM_MAX_INPUT_BYTES="120000"
export LLM_MAX_RETRIES="1"
# Optional dedicated-gateway fallback when OPENAI_API_KEY is unset.
export MIGRATION_INTELLIGENCE_URL="https://your-gateway.example"
export MIGRATION_INTELLIGENCE_TOKEN="..."
export SANDBOX_EXECUTOR_URL="https://your-sandbox-worker.example.workers.dev"
export SANDBOX_EXECUTOR_TOKEN="..."
# Enable only after the Phase 4 isolation checklist has passed.
export SANDBOX_EXECUTION_ENABLED="true"
# Enable only after GitHub App write permissions are reauthorized and tested.
export GITHUB_PUBLISHING_ENABLED="true"
# Protect the aggregate Prometheus scrape endpoint with a separate credential.
export METRICS_BEARER_TOKEN="..."
# Optional per-attempt Phase 7 resource budgets (shown with defaults).
export GENERATION_MAX_CONTEXT_BYTES="100000"
export GENERATION_MAX_PROPOSAL_BYTES="250000"
export GENERATION_MAX_CHECK_TIMEOUT_MS="600000"
export GENERATION_MAX_SANDBOX_DURATION_MS="600000"
```

Do not commit those values. Basic pages and the signed-out live-API state work
without GitHub credentials.

For Railway, set `OPENAI_API_KEY` on the background `worker` service only. The
browser and Vercel frontend never need the key. GPT-4o enrichments are optional:
if the key is absent, AI migration generation is unavailable. Migration
generation requires either the OpenAI key or the dedicated-gateway variables.

The authenticated dashboard opens at `/migrations`. With
`NEXT_PUBLIC_DELTA_CODE_API_URL` unset it uses clearly labeled preview
migrations; with a configured API, failed authenticated requests stay visible
as errors and are never replaced with preview data. State-changing migration
actions also require `FRONTEND_URL` to match the browser origin because that
origin is the session-cookie CSRF boundary.

`ARTIFACT_STORAGE_ROOT` holds immutable Phase 2 source captures and Phase 4
patch artifacts. The local path is ignored by Git. Hosted ingestion and
publishing require an encrypted persistent volume; an ephemeral path is
suitable only for tests and local evaluation.

Private repository verification additionally requires the GitHub App to have
read-only **Contents** permission. Install or update the app on the private
repository, then use **Refresh repository access** in the dashboard settings
to repeat GitHub authorization and refresh the session's repository list.
The refreshed OAuth session records each repository's clone URL, default
branch, and GitHub App installation id; existing sessions must be refreshed
before Phase 3 jobs can check out those repositories.

Phase 5 publishing additionally requires explicit GitHub App installation
reauthorization for **Contents: read and write**, **Pull requests: read and
write**, and **Checks: read and write**. Keep `GITHUB_PUBLISHING_ENABLED`
unset until the controlled-repository checklist in the
[Phase 5 architecture note](architecture/phase-5-github-publishing.md) passes.

## Test and build

Run the same checks used in CI:

```bash
make test
```

Individual commands:

```bash
make lint
make test-backend
make test-frontend
make test-sandbox
make benchmark
make build
make health
```

The backend integration test starts temporary demo FastAPI servers on local
ephemeral ports. If a restricted shell forbids local socket binding, run the
test from a normal terminal.

`GET /health` is the liveness endpoint. `GET /ready` checks PostgreSQL and
reports optional feature-gate configuration without exposing secret values.
`GET /metrics` requires `Authorization: Bearer $METRICS_BEARER_TOKEN` and
returns only fixed-cardinality aggregate job outcomes and durations.

## Database operations

```bash
make db-up
make db-schema
make db-logs
make db-down
```

`db-down` stops containers but preserves the named PostgreSQL volume.

## Deploy the hosted services

Authenticate the Railway and Vercel CLIs before the first command-line
deployment. The Railway project must be linked to this repository and contain
services named `web` and `worker`. The Vercel project should be named
`deltacode` and use `frontend` as its root directory.

Deploy both Railway services and wait for each build to finish:

```bash
make deploy-backend
```

Deploy only one service:

```bash
make deploy-web
make deploy-worker
```

Build and deploy the native Next.js frontend to Vercel production:

```bash
make deploy-frontend
```

Deploy all three services:

```bash
make deploy
```

Use another Railway environment with:

```bash
make deploy-backend RAILWAY_ENV=staging
```

After deployment, verify the public backend:

```bash
make health-live
```

Set `NEXT_PUBLIC_DELTA_CODE_API_URL` in Vercel. Set Railway's `FRONTEND_URL` to
the canonical Vercel production URL, and add any additional trusted preview
origins to `ALLOWED_ORIGINS` as a comma-separated list.

The production service variables remain managed by Railway and Vercel;
the Makefile never embeds database credentials, GitHub secrets, or private
keys.
