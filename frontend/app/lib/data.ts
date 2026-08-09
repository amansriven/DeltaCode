export type RunStatus = "pending" | "running" | "done" | "failed";
export type FindingKind = "regression" | "status_code_changed";

export interface ApiRequest {
  method: string;
  path: string;
  json?: unknown;
}

export interface ApiResponse {
  status_code: number;
  body: unknown;
}

export interface Finding {
  case: string;
  kind: FindingKind;
  request: ApiRequest;
  base_response: ApiResponse;
  pr_response: ApiResponse;
}

export interface RunSummary {
  id: number;
  repo: string;
  pr_number: number | null;
  status: RunStatus;
  created_at: string;
  finding_count?: number;
  highest_severity?: FindingKind | "none";
}

export interface RunDetail extends RunSummary {
  result: { findings: Finding[] } | null;
  updated_at: string;
  base_ref?: string;
  base_sha?: string;
  head_ref?: string;
  head_sha?: string;
  error?: string;
}

export type AiTriageStatus = "not_generated" | "queued" | "running" | "ready" | "failed";

export interface AiTriagePriority {
  run_id: number;
  urgency: "high" | "medium" | "low";
  title: string;
  reason: string;
}

export interface AiTriageBrief {
  headline: string;
  summary: string;
  priorities: AiTriagePriority[];
  watch_items: string[];
  model: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
}

export interface AiTriageResponse {
  status: AiTriageStatus;
  run_digest: string;
  run_count: number;
  updated_at?: string;
  brief?: AiTriageBrief;
  error_code?: string;
}

const omittedDiscount: Finding = {
  case: "omit_discount",
  kind: "regression",
  request: {
    method: "POST",
    path: "/items",
    json: { name: "example", price: 1.0 },
  },
  base_response: {
    status_code: 201,
    body: { name: "example", price: 1.0, discount: 0.0 },
  },
  pr_response: {
    status_code: 422,
    body: {
      detail: [
        {
          loc: ["body", "discount"],
          msg: "Field required",
          type: "missing",
          input: { name: "example", price: 1.0 },
        },
      ],
    },
  },
};

const missingItem: Finding = {
  case: "unknown_item_id",
  kind: "status_code_changed",
  request: {
    method: "GET",
    path: "/items/unknown-item",
  },
  base_response: {
    status_code: 404,
    body: { detail: "Item not found" },
  },
  pr_response: {
    status_code: 200,
    body: { id: null, name: null },
  },
};

export const demoRuns: RunSummary[] = [
  {
    id: 15,
    repo: "acme/delta-code-demo-app",
    pr_number: 4,
    status: "running",
    created_at: "2026-07-27T14:42:08.000Z",
  },
  {
    id: 14,
    repo: "acme/delta-code-demo-app",
    pr_number: 1,
    status: "done",
    created_at: "2026-07-27T13:51:34.538Z",
    finding_count: 1,
    highest_severity: "regression",
  },
  {
    id: 13,
    repo: "acme/inventory-api",
    pr_number: 28,
    status: "done",
    created_at: "2026-07-27T12:49:18.073Z",
    finding_count: 0,
    highest_severity: "none",
  },
  {
    id: 12,
    repo: "acme/payments-service",
    pr_number: 83,
    status: "done",
    created_at: "2026-07-26T20:12:04.000Z",
    finding_count: 1,
    highest_severity: "status_code_changed",
  },
  {
    id: 11,
    repo: "acme/inventory-api",
    pr_number: 27,
    status: "pending",
    created_at: "2026-07-26T18:02:41.000Z",
  },
];

export const demoDetails: Record<number, RunDetail> = {
  14: {
    ...demoRuns[1],
    result: { findings: [omittedDiscount] },
    updated_at: "2026-07-27T13:51:37.338Z",
    base_ref: "main",
    base_sha: "a61f3d9",
    head_ref: "fix/item-discounts",
    head_sha: "c82d711",
  },
  12: {
    ...demoRuns[3],
    result: { findings: [missingItem] },
    updated_at: "2026-07-26T20:12:12.000Z",
    base_ref: "main",
    base_sha: "3148cb1",
    head_ref: "refactor/item-lookup",
    head_sha: "74f6d02",
  },
  13: {
    ...demoRuns[2],
    result: { findings: [] },
    updated_at: "2026-07-27T12:49:26.000Z",
    base_ref: "main",
    base_sha: "8be31a4",
    head_ref: "feat/warehouse-filter",
    head_sha: "fd77c02",
  },
  15: {
    ...demoRuns[0],
    result: null,
    updated_at: "2026-07-27T14:42:10.000Z",
    base_ref: "main",
    head_ref: "fix/query-validation",
  },
  11: {
    ...demoRuns[4],
    result: null,
    updated_at: "2026-07-26T18:02:41.000Z",
  },
};

export const demoAiTriage: AiTriageResponse = {
  status: "ready",
  run_digest: "preview",
  run_count: demoRuns.length,
  updated_at: "2026-07-27T14:45:00.000Z",
  brief: {
    headline: "Two verification runs deserve attention",
    summary:
      "Review the reproduced request regression first, then confirm whether the missing-item status change is intentional.",
    priorities: [
      {
        run_id: 14,
        urgency: "high",
        title: "Required discount may break existing clients",
        reason: "Run 14 reproduced the same request changing from 201 on base to 422 on the PR.",
      },
      {
        run_id: 12,
        urgency: "medium",
        title: "Missing-item behavior changed",
        reason: "Run 12 observed a missing item change from 404 to 200.",
      },
    ],
    watch_items: ["Run 15 is still in progress and is not included in a final verdict."],
    model: "gpt-4o",
    input_tokens: 438,
    output_tokens: 126,
    estimated_cost_usd: 0.002355,
  },
};

export const liveApiUrl =
  process.env.NEXT_PUBLIC_DELTA_CODE_API_URL?.replace(/\/$/, "") ?? "";

export type ApiConnectionStatus =
  | "preview"
  | "checking"
  | "connected"
  | "upgrade-required"
  | "unavailable";

interface HealthResponse {
  status?: string;
  capabilities?: unknown;
}

export async function checkApiConnection(
  signal?: AbortSignal,
): Promise<"connected" | "upgrade-required" | "unavailable"> {
  if (!liveApiUrl) return "unavailable";

  const response = await fetch(`${liveApiUrl}/health`, { signal });
  if (!response.ok) return "unavailable";

  const health = await response.json().catch(() => null) as HealthResponse | null;
  if (health?.status !== "ok") return "unavailable";

  const capabilities = Array.isArray(health.capabilities)
    ? health.capabilities.filter((item): item is string => typeof item === "string")
    : [];
  return capabilities.includes("migrations") && capabilities.includes("providers")
    ? "connected"
    : "upgrade-required";
}

export async function fetchRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  const response = await fetch(`${liveApiUrl}/runs`, {
    signal,
    credentials: "include",
  });
  if (response.status === 401) {
    throw new Error("Sign in with GitHub to see your runs.");
  }
  if (!response.ok) {
    throw new Error(`Runs request failed with ${response.status}`);
  }
  return response.json();
}

async function parseAiTriageResponse(response: Response): Promise<AiTriageResponse> {
  if (response.status === 401) {
    throw new Error("Sign in with GitHub to use AI triage.");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `AI triage request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchAiTriage(signal?: AbortSignal): Promise<AiTriageResponse> {
  const response = await fetch(`${liveApiUrl}/dashboard/ai-triage`, {
    signal,
    credentials: "include",
    cache: "no-store",
  });
  return parseAiTriageResponse(response);
}

export async function generateAiTriage(): Promise<AiTriageResponse> {
  const response = await fetch(`${liveApiUrl}/dashboard/ai-triage`, {
    method: "POST",
    credentials: "include",
  });
  return parseAiTriageResponse(response);
}

export async function fetchRun(
  runId: number,
  signal?: AbortSignal,
): Promise<RunDetail> {
  const response = await fetch(`${liveApiUrl}/runs/${runId}`, {
    signal,
    credentials: "include",
  });
  if (response.status === 401) {
    throw new Error("Sign in with GitHub to see this run.");
  }
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? "This run could not be found."
        : `Run request failed with ${response.status}`,
    );
  }
  return response.json();
}

export async function retryRun(
  runId: number,
): Promise<{ id: number; status: "pending" }> {
  const response = await fetch(`${liveApiUrl}/runs/${runId}/retry`, {
    method: "POST",
    credentials: "include",
  });
  if (response.status === 401) {
    throw new Error("Sign in with GitHub to retry this run.");
  }
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? "This run could not be found."
        : `Retry request failed with ${response.status}`,
    );
  }
  return response.json();
}

const githubLoginBaseUrl = `${liveApiUrl}/auth/github/login`;
export const githubLoginUrl =
  `${githubLoginBaseUrl}?redirect_uri=${encodeURIComponent("/migrations")}`;
export const githubRepositoryRefreshUrl =
  `${githubLoginBaseUrl}?redirect_uri=${encodeURIComponent("/settings/integrations")}`;

export interface CurrentUser {
  login: string;
  name: string | null;
  avatar_url: string | null;
  accessible_repos: string[];
  repositories?: RepositoryAccess[];
}

export interface RepositoryAccess {
  full_name: string;
  private: boolean | null;
  visibility: "public" | "private" | "internal" | "unknown" | string;
}

export async function fetchMe(signal?: AbortSignal): Promise<CurrentUser | null> {
  if (!liveApiUrl) return null;
  const response = await fetch(`${liveApiUrl}/auth/me`, {
    signal,
    credentials: "include",
  });
  if (!response.ok) return null;
  return response.json();
}

export async function signOut(): Promise<void> {
  await fetch(`${liveApiUrl}/auth/logout`, {
    method: "POST",
    credentials: "include",
    redirect: "manual",
  });
}
