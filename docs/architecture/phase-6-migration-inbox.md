# Phase 6: migration inbox

Phase 6 makes the provider-neutral control plane the authenticated product
experience. GitHub sign-in returns developers to `/migrations`.

## Implemented information architecture

| Route | Purpose | Backend contracts |
| --- | --- | --- |
| `/migrations` | Cursor-paged, searchable, filterable migration inbox | `GET /migrations` |
| `/migrations/{id}` | Repository impact, plan, patch, checks, attempts, recommendation, and actions | `GET /migrations/{id}`, `GET /changes/{id}`, `GET /migrations/{id}/publication` |
| `/changes/{id}` | Authoritative sources and normalized before/after semantics | `GET /changes/{id}` |
| `/providers` | Provider/source health and synchronization entry point | `GET /providers` |

The inbox answers the product RFC's core questions in one scan: provider,
repository, risk, deadline, automation state, draft-PR state, and required
developer attention. Client-side filters operate on the current cursor page;
the **Load more** action preserves the server's opaque cursor contract.

## Evidence and attempts

The migration detail renders stored structured evidence rather than deriving
claims from a patch in the browser. It includes:

- authoritative provider source links and normalized change semantics;
- dependency/call-site reasoning, analyzer method, confidence, and coverage;
- the migration plan and expected paths;
- content-addressed patch metadata, changed files, and test intent;
- deterministic verification checks with bounded display logs;
- model review, recommendation, confidence, and unresolved uncertainty;
- immutable attempt history, revision reasons, and developer instructions;
- publication branch, progress, and draft pull-request link when present.

Selecting an older attempt changes the evidence being inspected without
mutating the migration or rewriting attempt history.

## Developer actions

The detail page uses the existing optimistic and idempotent mutation APIs:

- `POST /migrations/{id}/generate`
- `POST /migrations/{id}/publish`
- `POST /migrations/{id}/approve`
- `POST /migrations/{id}/revise`
- `POST /migrations/{id}/snooze`
- `POST /migrations/{id}/decline`

Every request sends the visible migration version and a unique idempotency key.
Revision and decline require a recorded reason; revision additionally requires
instructions; snooze requires a timestamp. A `409` conflict is shown to the
developer rather than silently retrying against newer evidence.

Approve retains the Phase 0 boundary: it marks a draft pull request ready for
the repository's normal review process and never merges it.

## Live progress and failure states

Planning, generation, verification, and publication states are visible in a
progress rail. Active migration details refresh every five seconds. Blocked
migrations show their safe error code and expose retry only through a valid
state transition. Missing publication records are treated as an expected
pre-publication state.

The dashboard also surfaces the three operational boundaries that determine
whether an action can succeed: repository analysis, sandbox policy, and GitHub
write permissions. The backend remains authoritative and fail-closed for both
sandbox execution and GitHub publishing.

## Preview behavior

When `NEXT_PUBLIC_DELTA_CODE_API_URL` is unset, the UI uses clearly labeled,
representative migration fixtures. Preview actions update only local component
state. A failed live request is never replaced with fixture data.

## Accessibility and responsive behavior

The inbox uses semantic tables on wide screens and equivalent labeled cards on
narrow screens. Controls have visible focus, explicit labels, text status in
addition to color, reduced-motion behavior, keyboard-operable disclosure
panels, and responsive action/form layouts. Live polling does not steal focus
or announce the entire page repeatedly.

## Phase 6 exit criteria

1. Migration work is the first authenticated destination.
2. Inbox rows expose provider, repository, risk, deadline, and action state.
3. A developer can trace a recommendation from authoritative change through
   call sites, patch, tests, checks, and review.
4. Attempts and revision instructions remain inspectable and immutable.
5. Generate, publish, approve, revise, snooze, and decline use the secured
   server mutations.
6. Active and blocked states are explicit and recoverable.
7. The production frontend build, lint, and server-render tests cover the new
   routes.
