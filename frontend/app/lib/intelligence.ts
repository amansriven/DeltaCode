import { apiBaseUrl, liveApiUrl } from "./data";

export type BriefStatus = "not_generated" | "queued" | "running" | "ready" | "failed";

export interface WorkspaceBrief {
  headline: string;
  executive_summary: string;
  attention_summary: string;
  priorities: Array<{
    migration_id: string;
    title: string;
    urgency: "critical" | "high" | "medium" | "low";
    recommended_action: "review" | "generate" | "revise" | "publish" | "monitor";
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

export const demoWorkspaceBrief: WorkspaceBriefResponse = {
  status: "ready",
  configured: true,
  migration_digest: "preview",
  migration_count: 4,
  model: "gpt-4o",
  updated_at: "2026-08-15T15:30:00Z",
  usage: {
    input_tokens: 1834,
    cached_input_tokens: 0,
    output_tokens: 518,
    estimated_cost_usd: 0.009765,
  },
  brief: {
    headline: "Two migrations need a decision before the next provider deadline",
    executive_summary: "The portfolio is stable, but the Stripe checkout migration is waiting for review and the Auth0 token change remains blocked on a failed integration check. Resolving those two items clears the highest-risk work; the remaining migrations can stay in the normal queue.",
    attention_summary: "2 decisions · 1 blocked migration · 1 draft PR ready",
    priorities: [
      {
        migration_id: "migration-checkout-source",
        title: "Review the Stripe payment-source migration",
        urgency: "critical",
        recommended_action: "review",
        reason: "The provider deadline is near and a verified patch is ready for developer review.",
        evidence: ["High-risk provider change", "Verification checks completed", "Developer decision required"],
      },
      {
        migration_id: "migration-auth-token",
        title: "Unblock the Auth0 token revision",
        urgency: "high",
        recommended_action: "revise",
        reason: "The current attempt cannot advance while its integration check remains blocked.",
        evidence: ["Blocked migration state", "Integration check requires attention"],
      },
    ],
    portfolio_risks: [
      {
        title: "Deadline concentration",
        detail: "The highest-risk provider changes land in the same review window, increasing coordination pressure.",
        affected_migration_ids: ["migration-checkout-source", "migration-auth-token"],
      },
    ],
    next_actions: [
      { label: "Review verified checkout patch", detail: "Confirm evidence and approve or request a revision.", migration_id: "migration-checkout-source" },
      { label: "Resolve blocked integration check", detail: "Inspect the failed check before regenerating the Auth0 migration.", migration_id: "migration-auth-token" },
      { label: "Confirm repository coverage", detail: "Verify every critical provider has at least one connected repository.", migration_id: null },
    ],
  },
};

async function parseResponse(response: Response): Promise<WorkspaceBriefResponse> {
  if (response.status === 401) throw new Error("Sign in with GitHub to use AI briefing.");
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `AI briefing request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchWorkspaceBrief(signal?: AbortSignal): Promise<WorkspaceBriefResponse> {
  if (!liveApiUrl) return demoWorkspaceBrief;
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
