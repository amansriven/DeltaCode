import { apiBaseUrl, liveApiUrl } from "./data";

export type PullRequestOverviewStatus = "not_generated" | "queued" | "running" | "ready" | "failed";

export interface PullRequestSummary {
  repository_full_name: string;
  number: number;
  title: string;
  body_excerpt: string;
  state: string;
  draft: boolean;
  html_url: string;
  author: { login: string; avatar_url: string | null };
  base: { ref: string; sha: string };
  head: { ref: string; sha: string };
  created_at: string;
  updated_at: string;
  ai_overview: {
    status: PullRequestOverviewStatus;
    head_sha?: string | null;
    model?: string | null;
    updated_at?: string | null;
  };
}

export interface PullRequestOverview {
  verdict: "low_risk" | "review_needed" | "high_risk" | "insufficient_context";
  headline: string;
  executive_summary: string;
  change_summary: string[];
  risk_signals: Array<{
    severity: "high" | "medium" | "low";
    title: string;
    detail: string;
    evidence: string[];
  }>;
  review_focus: Array<{
    path: string | null;
    title: string;
    detail: string;
    reviewer_question: string;
  }>;
  test_assessment: {
    status: "adequate" | "gaps" | "unknown";
    summary: string;
    missing_coverage: string[];
  };
  recommended_actions: string[];
  confidence: { score: number; basis: string };
}

export interface PullRequestOverviewResponse {
  status: PullRequestOverviewStatus;
  configured: boolean;
  repository_full_name: string;
  pull_number: number;
  head_sha?: string | null;
  pull_updated_at?: string | null;
  overview?: PullRequestOverview | null;
  model?: string | null;
  usage?: {
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
  } | null;
  error_code?: string | null;
  updated_at?: string | null;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 401) throw new Error("Sign in with GitHub to review pull requests.");
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `Pull request request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchRecentPullRequests(signal?: AbortSignal): Promise<{
  items: PullRequestSummary[];
  configured: boolean;
}> {
  if (!liveApiUrl) throw new Error("The live Delta Code API is not configured.");
  const response = await fetch(`${apiBaseUrl}/pull-requests?limit=30`, {
    signal,
    credentials: "include",
    cache: "no-store",
  });
  return parseResponse(response);
}

function overviewUrl(repository: string, pullNumber: number): string {
  const [owner, name] = repository.split("/");
  return `${apiBaseUrl}/pull-requests/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${pullNumber}/overview`;
}

export async function fetchPullRequestOverview(
  repository: string,
  pullNumber: number,
  signal?: AbortSignal,
): Promise<PullRequestOverviewResponse> {
  const response = await fetch(overviewUrl(repository, pullNumber), {
    signal,
    credentials: "include",
    cache: "no-store",
  });
  return parseResponse(response);
}

export async function generatePullRequestOverview(
  repository: string,
  pullNumber: number,
  refresh = false,
): Promise<PullRequestOverviewResponse> {
  const response = await fetch(overviewUrl(repository, pullNumber), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  return parseResponse(response);
}
