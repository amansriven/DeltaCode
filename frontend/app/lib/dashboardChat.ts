import { apiBaseUrl } from "./data";

export interface DashboardChatMessage {
  id: string;
  role: "user" | "assistant";
  status: "ready" | "queued" | "running" | "failed";
  content: string | null;
  answer?: {
    scope_status: "answered" | "out_of_scope" | "insufficient_context";
    answer: string;
    citations: Array<{
      kind: "repository" | "provider" | "migration" | "pull_request" | "dashboard";
      label: string;
      href: string;
    }>;
    repository_sources?: Array<{
      repository_full_name: string;
      path: string;
      reason: string;
    }>;
    follow_ups: string[];
  } | null;
  model?: string | null;
  usage?: {
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
  } | null;
  error_code?: string | null;
  created_at: string;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 401) throw new Error("Sign in with GitHub to use Ask Delta.");
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `Ask Delta request failed with ${response.status}`);
  }
  return response.json();
}

export async function askDelta(
  message: string,
  repositories: string[],
  threadId: string | null,
): Promise<{ thread_id: string; message_id: string; status: "queued" }> {
  const response = await fetch(`${apiBaseUrl}/intelligence/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      thread_id: threadId,
      message,
      repository_full_names: repositories,
    }),
  });
  return parseResponse(response);
}

export async function fetchDeltaThread(
  threadId: string,
): Promise<{ thread_id: string; messages: DashboardChatMessage[]; configured: boolean }> {
  const response = await fetch(`${apiBaseUrl}/intelligence/chat/${encodeURIComponent(threadId)}`, {
    credentials: "include",
    cache: "no-store",
  });
  return parseResponse(response);
}
