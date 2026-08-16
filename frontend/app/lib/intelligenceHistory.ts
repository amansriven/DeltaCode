import { apiBaseUrl } from "./data";

export type IntelligenceHistoryKind = "all" | "chat" | "briefing" | "pull_request";

export interface IntelligenceUsage {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
}

export interface IntelligenceHistoryItem {
  id: string;
  kind: Exclude<IntelligenceHistoryKind, "all">;
  title: string;
  summary: string;
  status: string;
  repository_full_names: string[];
  created_at: string;
  updated_at: string;
  model: string | null;
  usage: IntelligenceUsage;
  metadata: Record<string, string | number | boolean | null>;
}

export interface ChatHistoryDetail {
  kind: "chat";
  id: string;
  messages: Array<{
    id: string;
    role: "user" | "assistant";
    status: string;
    content: string | null;
    answer: { answer?: string } | null;
    model: string | null;
    usage: IntelligenceUsage;
    error_code: string | null;
    created_at: string;
  }>;
}

export interface BriefingHistoryDetail {
  kind: "briefing";
  id: string;
  status: string;
  scope: { mode?: string; repository_full_names?: string[] };
  brief: null | {
    headline: string;
    executive_summary: string;
    attention_summary: string;
    priorities: Array<{
      title: string;
      urgency: string;
      reason: string;
      evidence: string[];
    }>;
    next_actions: Array<{ label: string; detail: string }>;
  };
  model: string | null;
  usage: IntelligenceUsage;
  created_at: string;
  updated_at: string;
}

export interface PullRequestHistoryDetail {
  kind: "pull_request";
  id: string;
  status: string;
  repository_full_name: string;
  pull_number: number;
  head_sha: string | null;
  pull_updated_at: string | null;
  snapshot: { title?: string; html_url?: string } | null;
  overview: null | {
    verdict: string;
    headline: string;
    executive_summary: string;
    change_summary: string[];
    recommended_actions: string[];
    confidence: { score: number; basis: string };
  };
  model: string | null;
  usage: IntelligenceUsage;
  created_at: string;
  updated_at: string;
}

export type IntelligenceHistoryDetail =
  | ChatHistoryDetail
  | BriefingHistoryDetail
  | PullRequestHistoryDetail;

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 401) throw new Error("Sign in with GitHub to view intelligence history.");
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `History request failed with ${response.status}`);
  }
  return response.json();
}

export async function fetchIntelligenceHistory(
  kind: IntelligenceHistoryKind,
  query = "",
  repository = "",
  cursor?: string | null,
  signal?: AbortSignal,
): Promise<{ items: IntelligenceHistoryItem[]; next_cursor: string | null }> {
  const parameters = new URLSearchParams({ kind, limit: "25" });
  if (query.trim()) parameters.set("query", query.trim());
  if (repository) parameters.set("repository", repository);
  if (cursor) parameters.set("cursor", cursor);
  const response = await fetch(`${apiBaseUrl}/intelligence/history?${parameters}`, {
    credentials: "include",
    cache: "no-store",
    signal,
  });
  return parseResponse(response);
}

export async function fetchIntelligenceHistoryDetail(
  item: IntelligenceHistoryItem,
  signal?: AbortSignal,
): Promise<IntelligenceHistoryDetail> {
  const parameters = new URLSearchParams({ item_id: item.id });
  const response = await fetch(
    `${apiBaseUrl}/intelligence/history/${item.kind}?${parameters}`,
    { credentials: "include", cache: "no-store", signal },
  );
  return parseResponse(response);
}
