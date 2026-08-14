export const liveApiUrl =
  process.env.NEXT_PUBLIC_DELTA_CODE_API_URL?.replace(/\/$/, "") ?? "";

// Browser requests stay on the Delta Code origin so authenticated cookies are
// first-party. Next.js forwards /api/* to the configured backend.
export const apiBaseUrl = liveApiUrl ? "/api" : "";

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

  const response = await fetch(`${apiBaseUrl}/health`, { signal });
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

const githubLoginBaseUrl = `${apiBaseUrl}/auth/github/login`;

export function githubLoginUrlFor(redirectPath: string): string {
  const safePath = redirectPath.startsWith("/") && !redirectPath.startsWith("//")
    ? redirectPath
    : "/migrations";
  return `${githubLoginBaseUrl}?redirect_uri=${encodeURIComponent(safePath)}`;
}

export const githubLoginUrl = githubLoginUrlFor("/migrations");
export const githubRepositoryRefreshUrl = githubLoginUrlFor("/settings/integrations");

export interface RepositoryAccess {
  full_name: string;
  private: boolean | null;
  visibility: "public" | "private" | "internal" | "unknown" | string;
}

export interface CurrentUser {
  login: string;
  name: string | null;
  avatar_url: string | null;
  accessible_repos: string[];
  repositories?: RepositoryAccess[];
}

export async function fetchMe(signal?: AbortSignal): Promise<CurrentUser | null> {
  if (!liveApiUrl) return null;
  const response = await fetch(`${apiBaseUrl}/auth/me`, {
    signal,
    credentials: "include",
  });
  if (!response.ok) return null;
  return response.json();
}

export async function signOut(): Promise<void> {
  await fetch(`${apiBaseUrl}/auth/logout`, {
    method: "POST",
    credentials: "include",
    redirect: "manual",
  });
}
