import { apiBaseUrl, liveApiUrl } from "./data";

export type BriefStatus = "not_generated" | "queued" | "running" | "ready" | "failed";

export interface WorkspaceBrief {
  headline: string;
  executive_summary: string;
  attention_summary: string;
  priorities: Array<{
    migration_id: string | null;
    title: string;
    urgency: "critical" | "high" | "medium" | "low";
    recommended_action: "connect" | "scan" | "review" | "generate" | "revise" | "publish" | "monitor";
    reason: string;
    evidence: string[];
  }>;
  portfolio_risks: Array<{
    title: string;
    detail: string;
    affected_migration_ids: string[];
  }>;
  next_actions: Array<{
    label: string;
    detail: string;
    migration_id: string | null;
  }>;
}

export interface WorkspaceBriefResponse {
  status: BriefStatus;
  configured: boolean;
  migration_digest: string;
  migration_count: number;
  repository_count: number;
  provider_count: number;
  source_count: number;
  change_count: number;
  brief?: WorkspaceBrief | null;
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

async function parseResponse(response: Response): Promise<WorkspaceBriefResponse> {
  if (response.status === 401) throw new Error("Sign in with GitHub to use AI briefing.");
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `AI briefing request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchWorkspaceBrief(signal?: AbortSignal): Promise<WorkspaceBriefResponse> {
  if (!liveApiUrl) throw new Error("The live Delta Code API is not configured.");
  const response = await fetch(`${apiBaseUrl}/intelligence/briefing`, {
    signal,
    credentials: "include",
    cache: "no-store",
  });
  return parseResponse(response);
}

export async function generateWorkspaceBrief(refresh = false): Promise<WorkspaceBriefResponse> {
  const response = await fetch(`${apiBaseUrl}/intelligence/briefing`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  return parseResponse(response);
}
