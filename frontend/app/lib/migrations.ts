import { apiBaseUrl } from "./data";

export type MigrationStatus =
  | "queued"
  | "planning"
  | "generating"
  | "verifying"
  | "ready"
  | "needs_revision"
  | "blocked"
  | "pr_opening"
  | "pr_opened"
  | "snoozed"
  | "declined"
  | "approved"
  | "completed";

export type Risk = "informational" | "low" | "medium" | "high" | "critical" | "unknown";
export type RecommendationAction = "approve" | "revise" | "snooze" | "decline";

export interface Confidence {
  score: number;
  basis: string;
  reasons?: string[];
  unresolved?: string[];
}

export interface MigrationSummary {
  id: string;
  change_event_id: string;
  repository_id: string;
  repository_full_name: string;
  provider_name: string;
  change_summary: string;
  risk: Risk | string;
  status: MigrationStatus | string;
  decision_state: string | null;
  current_attempt_id: string | null;
  pull_request_url: string | null;
  snoozed_until: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  effective_at?: string | null;
  error_code?: string | null;
}

export interface CallSite {
  id: string;
  path: string;
  start_line: number;
  end_line: number;
  language?: string | null;
  symbol?: string | null;
  target?: string | null;
  detection_method: string;
  reason: string;
  confidence: Confidence;
}

export interface VerificationCheck {
  id: string;
  kind: string;
  status: "passed" | "failed" | "skipped" | "timed_out" | "blocked" | "infrastructure_error";
  command?: string | null;
  duration_ms: number;
  summary: string;
  display_log?: string | null;
}

export interface MigrationEvidence {
  repository: {
    full_name: string;
    base_branch: string;
    base_commit_sha: string;
    snapshot_digest: string;
  };
  impact: {
    conclusion: string;
    summary: string;
    call_sites: CallSite[];
    coverage: {
      supported: boolean;
      languages: string[];
      files_considered: number;
      files_excluded: number;
      parse_failures: number;
      limitations: string[];
    };
    confidence: Confidence;
  };
  plan: {
    summary: string;
    steps: Array<{
      id: string;
      description: string;
      call_site_ids: string[];
      expected_paths: string[];
    }>;
    verification_strategy: string[];
    assumptions: string[];
    unresolved: string[];
  };
  patch: {
    artifact_id: string;
    sha256: string;
    summary: string;
    files: Array<{
      path: string;
      change_type: string;
      previous_path?: string | null;
      plan_step_ids: string[];
    }>;
  };
  tests: Array<{ path: string; action: string; purpose: string; provenance: string }>;
  verification_checks: VerificationCheck[];
  review: {
    summary: string;
    findings: Array<{
      severity: string;
      path?: string | null;
      line?: number | null;
      summary: string;
      resolved: boolean;
    }>;
    provenance: string;
    model: { id: string; version: string };
  };
  recommendation: {
    action: RecommendationAction;
    rationale: string;
    confidence: Confidence;
    unresolved: string[];
  };
  pull_request?: {
    number: number;
    url: string;
    draft: boolean;
    branch: string;
    head_sha: string;
    status: string;
  } | null;
  created_at: string;
  completed_at: string;
}

export interface AttemptSummary {
  id: string;
  number: number;
  status: string;
  recommendation: RecommendationAction | null;
  previous_attempt_id: string | null;
  revision_reason?: string | null;
  developer_instructions?: string | null;
  error_code?: string | null;
  evidence: MigrationEvidence | null;
  created_at: string;
  updated_at: string;
}

export interface MigrationDetail extends MigrationSummary {
  attempts: AttemptSummary[];
}

export interface ChangeDetail {
  id: string;
  provider: { id: string; name: string; product?: string | null };
  status: string;
  change_type: string;
  severity: string;
  breaking: boolean | null;
  summary: string;
  before?: unknown;
  after?: unknown;
  detected_at?: string;
  published_at?: string | null;
  effective_at?: string | null;
  source_artifacts?: Array<{
    id: string;
    source_type: string;
    canonical_url: string;
    captured_at: string;
    authoritative: true;
  }>;
  claims?: Array<{
    id: string;
    summary: string;
    provenance: string;
    locator?: string | null;
  }>;
  confidence: Confidence;
}

export interface PublicationStatus {
  status: "queued" | "publishing" | "completed" | "failed";
  branch: string;
  pull_number?: number | null;
  pull_url?: string | null;
  error_code?: string | null;
}

export interface ProviderSummary {
  id: string;
  name: string;
  product?: string | null;
  status: "active" | "paused" | "degraded" | "disconnected" | string;
  source_count: number;
  last_synced_at?: string | null;
  updated_at: string;
}

export interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

const now = "2026-08-06T15:10:00Z";
const baseEvidence: MigrationEvidence = {
  repository: {
    full_name: "acme/checkout-api",
    base_branch: "main",
    base_commit_sha: "0123456789abcdef0123456789abcdef01234567",
    snapshot_digest: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  },
  impact: {
    conclusion: "affected",
    summary: "The repository sends the removed source field in one charge-creation call site.",
    call_sites: [
      {
        id: "callsite-charge-service-42",
        path: "src/payments/charge-service.ts",
        start_line: 42,
        end_line: 48,
        language: "typescript",
        symbol: "client.charges.create",
        target: "POST /v1/charges",
        detection_method: "ast",
        reason: "The call constructs a charge request with the deprecated source field.",
        confidence: { score: 0.99, basis: "deterministic" },
      },
    ],
    coverage: {
      supported: true,
      languages: ["TypeScript"],
      files_considered: 84,
      files_excluded: 391,
      parse_failures: 0,
      limitations: ["Generated files and node_modules were excluded."],
    },
    confidence: { score: 0.98, basis: "deterministic" },
  },
  plan: {
    summary: "Replace source with payment_method and update the request-shape test fixture.",
    steps: [
      {
        id: "replace-request-field",
        description: "Pass paymentMethod as payment_method when creating a charge.",
        call_site_ids: ["callsite-charge-service-42"],
        expected_paths: ["src/payments/charge-service.ts"],
      },
      {
        id: "update-test-fixture",
        description: "Update the unit test to assert the new request shape.",
        call_site_ids: ["callsite-charge-service-42"],
        expected_paths: ["src/payments/charge-service.test.ts"],
      },
    ],
    verification_strategy: ["npm run lint", "npm run typecheck", "npm test"],
    assumptions: ["paymentMethod is already available to the service method."],
    unresolved: [],
  },
  patch: {
    artifact_id: "artifact-patch-001",
    sha256: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    summary: "Update the charge request and matching test fixture for the provider field migration.",
    files: [
      { path: "src/payments/charge-service.ts", change_type: "modified", plan_step_ids: ["replace-request-field"] },
      { path: "src/payments/charge-service.test.ts", change_type: "modified", plan_step_ids: ["update-test-fixture"] },
    ],
  },
  tests: [
    {
      path: "src/payments/charge-service.test.ts",
      action: "modified",
      purpose: "Prove the service sends payment_method and no longer sends source.",
      provenance: "existing_modified",
    },
  ],
  verification_checks: [
    { id: "lint", kind: "lint", status: "passed", command: "npm run lint", duration_ms: 1820, summary: "Lint completed without errors.", display_log: "Lint passed." },
    { id: "typecheck", kind: "type_check", status: "passed", command: "npm run typecheck", duration_ms: 2310, summary: "Type-check completed without errors.", display_log: "Type-check passed." },
    { id: "tests", kind: "unit_test", status: "passed", command: "npm test", duration_ms: 4920, summary: "All 42 unit tests passed.", display_log: "42 passed, 0 failed." },
  ],
  review: {
    summary: "The patch is limited to the affected request and its unit test; every configured check passes.",
    findings: [],
    provenance: "model_inferred",
    model: { id: "preview-review-model", version: "mock-1" },
  },
  recommendation: {
    action: "approve",
    rationale: "The affected call site was updated, its request-shape test was strengthened, and every deterministic check passed.",
    confidence: { score: 0.95, basis: "mixed" },
    unresolved: ["No live provider test environment was configured for behavioral verification."],
  },
  pull_request: null,
  created_at: "2026-08-06T14:42:00Z",
  completed_at: "2026-08-06T14:44:05Z",
};

export const demoMigrations: MigrationSummary[] = [
  {
    id: "migration-checkout-source",
    change_event_id: "change-payments-source",
    repository_id: "repo-checkout-api",
    repository_full_name: "acme/checkout-api",
    provider_name: "ExamplePay",
    change_summary: "Charge requests must use payment_method instead of source.",
    risk: "high",
    status: "ready",
    decision_state: null,
    current_attempt_id: "attempt-checkout-2",
    pull_request_url: null,
    snoozed_until: null,
    version: 8,
    created_at: "2026-08-06T14:38:00Z",
    updated_at: "2026-08-06T14:44:05Z",
    effective_at: "2026-08-20T00:00:00Z",
  },
  {
    id: "migration-auth-token",
    change_event_id: "change-auth-token",
    repository_id: "repo-identity-service",
    repository_full_name: "acme/identity-service",
    provider_name: "ExampleID",
    change_summary: "Machine tokens now require an explicit audience claim.",
    risk: "critical",
    status: "pr_opened",
    decision_state: null,
    current_attempt_id: "attempt-auth-1",
    pull_request_url: "https://github.com/acme/identity-service/pull/418",
    snoozed_until: null,
    version: 10,
    created_at: "2026-08-05T15:18:00Z",
    updated_at: "2026-08-06T12:02:00Z",
    effective_at: "2026-08-12T00:00:00Z",
  },
  {
    id: "migration-webhook-signature",
    change_event_id: "change-webhook-signature",
    repository_id: "repo-developer-portal",
    repository_full_name: "acme/developer-portal",
    provider_name: "Hookdeck",
    change_summary: "Webhook verification moves from SHA-1 to SHA-256 signatures.",
    risk: "high",
    status: "generating",
    decision_state: null,
    current_attempt_id: "attempt-hookdeck-1",
    pull_request_url: null,
    snoozed_until: null,
    version: 4,
    created_at: "2026-08-06T13:55:00Z",
    updated_at: now,
    effective_at: "2026-09-01T00:00:00Z",
  },
  {
    id: "migration-inventory-client",
    change_event_id: "change-inventory-client",
    repository_id: "repo-inventory-worker",
    repository_full_name: "acme/inventory-worker",
    provider_name: "StockFlow",
    change_summary: "The legacy inventory batch client is removed in SDK 6.",
    risk: "high",
    status: "blocked",
    decision_state: null,
    current_attempt_id: "attempt-inventory-1",
    pull_request_url: null,
    snoozed_until: null,
    version: 6,
    created_at: "2026-08-04T09:20:00Z",
    updated_at: "2026-08-06T10:11:00Z",
    effective_at: "2026-08-15T00:00:00Z",
    error_code: "sandbox_dependency_install_failed",
  },
  {
    id: "migration-sms-region",
    change_event_id: "change-sms-region",
    repository_id: "repo-notifications",
    repository_full_name: "acme/notifications",
    provider_name: "MessageBird",
    change_summary: "Regional message routing becomes mandatory for EU senders.",
    risk: "medium",
    status: "snoozed",
    decision_state: "snooze",
    current_attempt_id: "attempt-message-1",
    pull_request_url: null,
    snoozed_until: "2026-08-19T13:00:00Z",
    version: 7,
    created_at: "2026-08-02T11:00:00Z",
    updated_at: "2026-08-05T17:22:00Z",
    effective_at: "2026-10-01T00:00:00Z",
  },
];

export const demoProviders: ProviderSummary[] = [...new Set(demoMigrations.map((item) => item.provider_name))].map((name, index) => ({
  id: `provider-${name.toLowerCase()}`,
  name,
  status: index === 3 ? "degraded" : "active",
  source_count: index % 2 + 1,
  last_synced_at: demoMigrations.find((item) => item.provider_name === name)?.updated_at || now,
  updated_at: now,
}));

const checkoutAttempts: AttemptSummary[] = [
  {
    id: "attempt-checkout-2",
    number: 2,
    status: "completed",
    recommendation: "approve",
    previous_attempt_id: "attempt-checkout-1",
    revision_reason: "Keep the provider update inside the existing request adapter.",
    developer_instructions: "Update the adapter and its contract test; do not alter call sites outside payments.",
    evidence: baseEvidence,
    created_at: "2026-08-06T14:42:00Z",
    updated_at: "2026-08-06T14:44:05Z",
  },
  {
    id: "attempt-checkout-1",
    number: 1,
    status: "completed",
    recommendation: "revise",
    previous_attempt_id: null,
    evidence: {
      ...baseEvidence,
      review: {
        ...baseEvidence.review,
        summary: "The first patch bypassed the repository request adapter and duplicated mapping logic.",
        findings: [
          { severity: "medium", path: "src/payments/charge-service.ts", line: 44, summary: "Use the existing request adapter.", resolved: false },
        ],
      },
      recommendation: {
        action: "revise",
        rationale: "The behavior is correct, but the change should preserve the repository abstraction.",
        confidence: { score: 0.91, basis: "mixed" },
        unresolved: [],
      },
      completed_at: "2026-08-06T14:21:05Z",
    },
    created_at: "2026-08-06T14:18:00Z",
    updated_at: "2026-08-06T14:21:05Z",
  },
];

export const demoMigrationDetails: Record<string, MigrationDetail> = Object.fromEntries(
  demoMigrations.map((migration) => [
    migration.id,
    {
      ...migration,
      attempts:
        migration.id === "migration-checkout-source"
          ? checkoutAttempts
          : [
              {
                id: migration.current_attempt_id ?? `attempt-${migration.id}`,
                number: 1,
                status: migration.status === "generating" ? "generating" : migration.status === "blocked" ? "failed" : "completed",
                recommendation: migration.status === "pr_opened" ? "approve" : migration.status === "snoozed" ? "snooze" : null,
                previous_attempt_id: null,
                error_code: migration.error_code,
                evidence: migration.status === "pr_opened" || migration.status === "snoozed" ? { ...baseEvidence, repository: { ...baseEvidence.repository, full_name: migration.repository_full_name } } : null,
                created_at: migration.created_at,
                updated_at: migration.updated_at,
              },
            ],
    },
  ]),
);

export const demoChanges: Record<string, ChangeDetail> = Object.fromEntries(
  demoMigrations.map((migration) => [
    migration.change_event_id,
    {
      id: migration.change_event_id,
      provider: { id: migration.provider_name.toLowerCase(), name: migration.provider_name },
      status: "ready",
      change_type: "sdk_symbol_changed",
      severity: migration.risk,
      breaking: migration.risk === "high" || migration.risk === "critical",
      summary: migration.change_summary,
      before: { field: "source", required: false },
      after: { field: "payment_method", required: true },
      detected_at: migration.created_at,
      effective_at: migration.effective_at,
      source_artifacts: [
        {
          id: `artifact-${migration.change_event_id}`,
          source_type: "migration_guide",
          canonical_url: "https://example.com/provider-migration-guide",
          captured_at: migration.created_at,
          authoritative: true,
        },
      ],
      claims: [
        {
          id: `claim-${migration.change_event_id}`,
          summary: migration.change_summary,
          provenance: "provider_stated",
          locator: "Migration guide",
        },
      ],
      confidence: { score: 0.96, basis: "mixed", reasons: ["The provider migration guide and repository analysis agree."] },
    },
  ]),
);

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (response.status === 401) throw new Error("Sign in with GitHub to view migration work.");
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `${fallback} (${response.status})`);
  }
  return response.json();
}

export async function fetchMigrations(cursor?: string, signal?: AbortSignal): Promise<CursorPage<MigrationSummary>> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(`${apiBaseUrl}/migrations?${params}`, { signal, credentials: "include" });
  if (response.status === 404) {
    throw new Error("The connected API needs to be upgraded before migrations are available.");
  }
  return readJson(response, "Migrations could not be loaded");
}

export async function fetchMigration(id: string, signal?: AbortSignal): Promise<MigrationDetail> {
  const response = await fetch(`${apiBaseUrl}/migrations/${encodeURIComponent(id)}`, { signal, credentials: "include" });
  return readJson(response, "Migration could not be loaded");
}

export async function fetchChange(id: string, signal?: AbortSignal): Promise<ChangeDetail> {
  const response = await fetch(`${apiBaseUrl}/changes/${encodeURIComponent(id)}`, { signal, credentials: "include" });
  return readJson(response, "Provider change could not be loaded");
}

export async function fetchPublication(id: string, signal?: AbortSignal): Promise<PublicationStatus | null> {
  const response = await fetch(`${apiBaseUrl}/migrations/${encodeURIComponent(id)}/publication`, { signal, credentials: "include" });
  if (response.status === 404) return null;
  return readJson(response, "Publication status could not be loaded");
}

export async function fetchProviders(signal?: AbortSignal): Promise<CursorPage<ProviderSummary>> {
  const response = await fetch(`${apiBaseUrl}/providers?limit=100`, { signal, credentials: "include" });
  if (response.status === 404) {
    throw new Error("The connected API needs to be upgraded before providers are available.");
  }
  return readJson(response, "Providers could not be loaded");
}

function idempotencyKey(action: string, migrationId: string): string {
  return `${action}-${migrationId}-${crypto.randomUUID()}`;
}

export async function runMigrationCommand(
  migrationId: string,
  action: "generate" | "publish" | RecommendationAction,
  expectedVersion: number,
  input: { reason?: string; instructions?: string; snooze_until?: string } = {},
): Promise<MigrationSummary | { status: string; version: number }> {
  const response = await fetch(`${apiBaseUrl}/migrations/${encodeURIComponent(migrationId)}/${action}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey(action, migrationId),
    },
    body: JSON.stringify({ expected_version: expectedVersion, ...input }),
  });
  return readJson(response, `The ${action} action failed`);
}
