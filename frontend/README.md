# Delta Code frontend

The Delta Code frontend presents an AI-powered, Dependabot-style API migration
review bot. The public site explains the provider-change-to-draft-PR workflow;
the authenticated product provides the migration review inbox, provider source
operations, repository impact evidence, generated patch review, deterministic
verification, and developer-controlled GitHub publishing.

The application uses React 19, TypeScript, and Next.js and is deployed on
Vercel.

## Product message

The primary public promise is:

> **The AI review bot for breaking API changes.**

Supporting copy should reinforce the complete workflow:

> Watch official changes → find affected code → generate and verify the
> migration → open a draft PR → developer decides.

The visual and editorial rules are defined in [`../DESIGN.md`](../DESIGN.md).

## Local development

Requirements:

- Node.js `>=22.13.0`
- the Delta Code API, locally or on Railway

Install dependencies and start the development server:

```bash
npm ci
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Without an API URL, the interface uses clearly labeled preview data. To connect
the local frontend to the hosted API, create `frontend/.env`:

```dotenv
NEXT_PUBLIC_DELTA_CODE_API_URL=https://web-production-e59907.up.railway.app
```

Authenticated requests use `credentials: "include"` because GitHub sessions
are stored in secure, first-party cookies. Browser API traffic uses the
same-origin `/api/*` proxy, which forwards to `NEXT_PUBLIC_DELTA_CODE_API_URL`.
The OpenAI key belongs only on the backend worker and must never use a
`NEXT_PUBLIC_` variable. In split web/worker deployments, set
`WORKSPACE_INTELLIGENCE_ENABLED=true` on the web service.

## Product routes

| Route | Purpose |
| --- | --- |
| `/` | AI review bot landing page |
| `/product` | Migration review capabilities and trust model |
| `/how-it-works` | Provider-change-to-draft-PR workflow |
| `/docs` | Local setup, API, and GPT-4o configuration |
| `/security` | Repository, sandbox, model, and evidence boundaries |
| `/migrations` | Migration review inbox |
| `/migrations/{id}` | Impact, plan, patch, checks, attempts, and decisions |
| `/intelligence` | Repository-scoped briefings, usage, and Ask Delta chat |
| `/pull-requests` | Recent GitHub pull requests and user-triggered AI overviews |
| `/history` | Previous Ask Delta chats, briefings, and versioned PR overviews |
| `/changes/{id}` | Normalized provider change and provenance |
| `/providers` | Official source health and synchronization |
| `/repositories` | GitHub App repository access |
| `/settings/integrations` | GitHub identity, installation, and permissions |
| `/settings/account` | Account identity and appearance |

Light and dark themes share semantic colors for AI activity, verified evidence,
warnings, blocked states, and destructive actions.

## Commands

- `npm run dev` — start local development
- `npm run lint` — run ESLint
- `npm run build` — create a production Next.js build
- `npm test` — build and verify all rendered routes
- `npx vercel deploy` — create a Vercel preview deployment
- `npx vercel deploy --prod` — deploy to Vercel production

## Vercel deployment

Import the repository into Vercel and configure:

- **Project Name:** `deltacode`
- **Framework Preset:** Next.js
- **Root Directory:** `frontend`
- **Production Branch:** `main`
- **Environment variable:**
  `NEXT_PUBLIC_DELTA_CODE_API_URL=https://web-production-e59907.up.railway.app`

After the first production deployment, copy the canonical Vercel URL into the
Railway web service's `FRONTEND_URL` variable and redeploy Railway. Preview
domains that need authenticated API access must also be included in Railway's
comma-separated `ALLOWED_ORIGINS` variable.
