"use client";

/* eslint-disable @next/next/no-html-link-for-pages -- Native anchors keep route transitions explicit in the catch-all app shell. */
import { useEffect, useMemo, useState } from "react";
import { githubLoginUrlFor, liveApiUrl } from "./lib/data";
import {
  fetchWorkspaceBrief,
  generateWorkspaceBrief,
  WorkspaceBriefResponse,
} from "./lib/intelligence";
import {
  AttemptSummary,
  ChangeDetail,
  demoChanges,
  demoMigrationDetails,
  demoMigrations,
  demoProviders,
  fetchChange,
  fetchMigration,
  fetchMigrations,
  fetchPublication,
  fetchProviders,
  MigrationDetail,
  MigrationEvidence,
  MigrationStatus,
  MigrationSummary,
  PublicationStatus,
  ProviderSummary,
  RecommendationAction,
  runMigrationCommand,
} from "./lib/migrations";

type InboxView = "all" | "review" | "active" | "blocked" | "snoozed" | "done";
type DetailTab = "evidence" | "patch" | "checks" | "history";
type PendingAction = RecommendationAction | null;

const activeStatuses = new Set(["planning", "generating", "verifying", "pr_opening"]);
const reviewStatuses = new Set(["ready", "pr_opened", "needs_revision"]);
const finishedStatuses = new Set(["approved", "completed", "declined"]);

const statusLabels: Record<string, string> = {
  queued: "Queued",
  planning: "Planning",
  generating: "Generating",
  verifying: "Verifying",
  ready: "Ready to publish",
  needs_revision: "Needs revision",
  blocked: "Blocked",
  pr_opening: "Opening draft PR",
  pr_opened: "Draft PR open",
  snoozed: "Snoozed",
  declined: "Declined",
  approved: "Approved",
  completed: "Complete",
};

function formatStatus(value: string) {
  return statusLabels[value] ?? value.replaceAll("_", " ");
}

function formatRelative(value: string) {
  const difference = Date.now() - new Date(value).getTime();
  const minutes = Math.max(1, Math.round(difference / 60_000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function formatDate(value?: string | null) {
  if (!value) return "Not scheduled";
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

function percentage(value: number) {
  return `${Math.round(value * 100)}%`;
}

function StatusPill({ status }: { status: string }) {
  const tone = activeStatuses.has(status)
    ? "active"
    : status === "blocked" || status === "needs_revision"
      ? "danger"
      : status === "ready" || status === "pr_opened"
        ? "review"
        : finishedStatuses.has(status)
          ? "complete"
          : "muted";
  return (
    <span className={`migration-status migration-status-${tone}`}>
      <i aria-hidden="true" />
      {formatStatus(status)}
    </span>
  );
}

function RiskPill({ risk }: { risk: string }) {
  return <span className={`risk-pill risk-${risk}`}>{risk}</span>;
}

function ReadinessBanner() {
  return (
    <aside className="readiness-banner" aria-label="Automation readiness">
      <div>
        <span className="readiness-icon" aria-hidden="true">⌁</span>
        <span><strong>Repository analysis</strong><small>Ready</small></span>
      </div>
      <div>
        <span className="readiness-icon" aria-hidden="true">□</span>
        <span><strong>Sandbox execution</strong><small>{liveApiUrl ? "Policy controlled" : "Preview"}</small></span>
      </div>
      <div>
        <span className="readiness-icon" aria-hidden="true">⑂</span>
        <span><strong>GitHub publishing</strong><small>{liveApiUrl ? "Permission checked per action" : "Preview"}</small></span>
      </div>
    </aside>
  );
}

function InboxMetrics({ migrations }: { migrations: MigrationSummary[] }) {
  const [currentTime] = useState(() => Date.now());
  const dueSoon = migrations.filter((item) => {
    if (!item.effective_at) return false;
    const days = (new Date(item.effective_at).getTime() - currentTime) / 86_400_000;
    return days >= 0 && days <= 14;
  }).length;
  const metrics = [
    { label: "Needs review", value: migrations.filter((item) => reviewStatuses.has(item.status)).length, note: "Developer decision required", tone: "review" },
    { label: "In progress", value: migrations.filter((item) => activeStatuses.has(item.status)).length, note: "Planning, generating, or publishing", tone: "active" },
    { label: "Blocked", value: migrations.filter((item) => item.status === "blocked").length, note: "Needs attention before retry", tone: "danger" },
    { label: "Effective in 14 days", value: dueSoon, note: "Across the current page", tone: "warning" },
  ];
  return (
    <section className="inbox-metrics" aria-label="Migration inbox summary">
      {metrics.map((metric) => (
        <article key={metric.label} className={`inbox-metric metric-${metric.tone}`}>
          <span>{metric.label}</span>
          <strong>{metric.value}</strong>
          <small>{metric.note}</small>
        </article>
      ))}
    </section>
  );
}

function BriefLoading() {
  return (
    <div className="brief-loading" role="status">
      <span className="ai-orbit" aria-hidden="true"><i /><i /><i /></span>
      <div><strong>Analyzing migration evidence</strong><small>Prioritizing deadlines, risk, checks, and developer decisions…</small></div>
    </div>
  );
}

export function WorkspaceIntelligence() {
  const [response, setResponse] = useState<WorkspaceBriefResponse | null>(null);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const responseStatus = response?.status;

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchWorkspaceBrief(controller.signal)
      .then(setResponse)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!liveApiUrl || !responseStatus || !["queued", "running"].includes(responseStatus)) return;
    const timer = window.setInterval(() => {
      fetchWorkspaceBrief()
        .then((next) => {
          setResponse(next);
          if (!["queued", "running"].includes(next.status)) setGenerating(false);
        })
        .catch((reason: Error) => {
          setError(reason.message);
          setGenerating(false);
        });
    }, 2200);
    return () => window.clearInterval(timer);
  }, [responseStatus]);

  async function generate(refresh: boolean) {
    setError("");
    setGenerating(true);
    try {
      setResponse(await generateWorkspaceBrief(refresh));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AI briefing could not be generated.");
      setGenerating(false);
    }
  }

  const brief = response?.brief;
  const usage = response?.usage;
  const workspaceSignals = (response?.migration_count || 0)
    + (response?.repository_count || 0)
    + (response?.provider_count || 0)
    + (response?.source_count || 0)
    + (response?.change_count || 0);
  const isWorking = generating || responseStatus === "queued" || responseStatus === "running";
  return (
    <div className="dashboard-content intelligence-content" id="main-content">
      <div className="intelligence-heading">
        <div>
          <span className="section-kicker">OpenAI workspace intelligence</span>
          <h1>AI briefing</h1>
          <p>Turn live migration evidence into an executive-ready priority brief.</p>
        </div>
        <div className="intelligence-heading-actions">
          <span className={`model-availability ${response?.configured ? "available" : "unavailable"}`}><i />{response?.configured ? "OpenAI configured" : !liveApiUrl ? "Backend required" : "API key required"}</span>
          <button className="button button-primary" type="button" disabled={!liveApiUrl || isWorking || loading || response?.configured === false || workspaceSignals === 0} onClick={() => generate(response?.status === "ready")}>
            {isWorking ? "Generating…" : response?.status === "ready" ? "Refresh briefing" : "Generate briefing"}
          </button>
        </div>
      </div>
      {error && <div className="error-state intelligence-error" role="alert"><span>!</span><div><h2>AI briefing unavailable</h2><p>{error}</p>{error.includes("Sign in") && <a className="button button-primary" href={githubLoginUrlFor("/intelligence")}>Continue with GitHub</a>}</div></div>}
      {!liveApiUrl ? (
        <section className="intelligence-empty">
          <span className="ai-empty-mark" aria-hidden="true">✦</span>
          <div><span className="section-kicker">Live data required</span><h2>Connect the Delta Code backend</h2><p>Set <code>NEXT_PUBLIC_DELTA_CODE_API_URL</code> to the Railway web-service URL. AI briefing never substitutes canned model output.</p></div>
          <a className="button button-quiet" href="/docs#ai">Open setup guide →</a>
        </section>
      ) : loading ? <BriefLoading /> : response?.configured === false ? (
        <section className="intelligence-empty">
          <span className="ai-empty-mark" aria-hidden="true">✦</span>
          <div><span className="section-kicker">One configuration step</span><h2>Connect the model layer</h2><p>Set <code>OPENAI_API_KEY</code> on the worker and <code>WORKSPACE_INTELLIGENCE_ENABLED=true</code> on the web service. Briefings use strict structured output and never expose the key to the browser.</p></div>
          <a className="button button-quiet" href="/docs#ai">Open setup guide →</a>
        </section>
      ) : isWorking ? <BriefLoading /> : !brief ? (
        <section className="intelligence-empty">
          <span className="ai-empty-mark" aria-hidden="true">✦</span>
          <div><span className="section-kicker">{response?.migration_count ? "Migration portfolio" : "Workspace readiness"}</span><h2>{response?.migration_count ? "Your evidence is ready to analyze" : response?.repository_count ? `${response.repository_count} repositories are ready for an AI readiness scan` : "Connect your first repository"}</h2><p>{response?.migration_count ? `${response.migration_count} migrations will be ranked by urgency, risk, and required action.` : response?.repository_count ? "Generate a real, evidence-grounded plan for connecting provider sources and producing the first migration." : "Install Delta Code on at least one repository to create a grounded workspace briefing."}</p></div>
          {workspaceSignals > 0 && <button className="button button-primary" type="button" onClick={() => generate(false)}>{response?.migration_count ? "Generate briefing" : "Generate readiness brief"}</button>}
        </section>
      ) : (
        <>
          <section className="brief-hero">
            <div className="brief-hero-copy"><span className="brief-live"><i /> Evidence-grounded briefing</span><h2>{brief.headline}</h2><p>{brief.executive_summary}</p><div className="brief-attention"><span>Attention now</span><strong>{brief.attention_summary}</strong></div></div>
            <aside className="brief-model-card"><div><span className="openai-mark">✦</span><span><small>Generated with</small><strong>{response.model || "gpt-4o"}</strong></span></div><dl><div><dt>{response.migration_count ? "Migrations" : "Repositories"}</dt><dd>{response.migration_count || response.repository_count}</dd></div><div><dt>Tokens</dt><dd>{((usage?.input_tokens || 0) + (usage?.output_tokens || 0)).toLocaleString()}</dd></div><div><dt>Est. cost</dt><dd>${(usage?.estimated_cost_usd || 0).toFixed(4)}</dd></div></dl><small>{response.updated_at ? `Updated ${new Date(response.updated_at).toLocaleString()}` : "Generated just now"}</small></aside>
          </section>
          <div className="intelligence-grid">
            <section className="brief-section priority-section">
              <div className="brief-section-heading"><div><span className="section-kicker">Ranked by the model</span><h2>Priority queue</h2></div><span>{brief.priorities.length} items</span></div>
              <div className="brief-priority-list">{brief.priorities.map((priority, index) => <article key={`${priority.migration_id || "readiness"}-${index}`} className={`brief-priority priority-${priority.urgency}`}><span className="priority-rank">{String(index + 1).padStart(2, "0")}</span><div><span className="priority-meta"><i />{priority.urgency} · {priority.recommended_action}</span><h3>{priority.title}</h3><p>{priority.reason}</p><ul>{priority.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div>{priority.migration_id ? <a className="priority-open" aria-label={`Open ${priority.title}`} href={`/migrations/${priority.migration_id}`}>→</a> : <span className="priority-open readiness-priority-mark" aria-hidden="true">✦</span>}</article>)}</div>
            </section>
            <aside className="brief-side-stack">
              <section className="brief-section"><div className="brief-section-heading"><div><span className="section-kicker">Across the portfolio</span><h2>Risk signals</h2></div></div><div className="portfolio-risk-list">{brief.portfolio_risks.map((risk) => <article key={risk.title}><span>!</span><div><strong>{risk.title}</strong><p>{risk.detail}</p><small>{risk.affected_migration_ids.length} linked {risk.affected_migration_ids.length === 1 ? "migration" : "migrations"}</small></div></article>)}</div></section>
              <section className="brief-section"><div className="brief-section-heading"><div><span className="section-kicker">Recommended sequence</span><h2>Next actions</h2></div></div><ol className="next-action-list">{brief.next_actions.map((action, index) => <li key={`${action.label}-${index}`}><i>{index + 1}</i><div><strong>{action.label}</strong><p>{action.detail}</p>{action.migration_id && <a href={`/migrations/${action.migration_id}`}>Open evidence →</a>}</div></li>)}</ol></section>
            </aside>
          </div>
          <footer className="brief-footnote"><span>✦</span><p><strong>AI interpretation, deterministic evidence.</strong> This briefing prioritizes stored migration records; it does not replace verification results or developer approval.</p></footer>
        </>
      )}
    </div>
  );
}

function MigrationTable({ migrations }: { migrations: MigrationSummary[] }) {
  if (!migrations.length) {
    return (
      <div className="migration-empty">
        <span aria-hidden="true">△</span>
        <h2>No migrations match this view</h2>
        <p>Clear a filter or search for another provider or repository.</p>
      </div>
    );
  }
  return (
    <div className="migration-table-wrap">
      <table className="migration-table">
        <thead>
          <tr>
            <th>Provider change</th>
            <th>Repository</th>
            <th>Risk</th>
            <th>Effective</th>
            <th>Status</th>
            <th><span className="sr-only">Open</span></th>
          </tr>
        </thead>
        <tbody>
          {migrations.map((migration) => (
            <tr key={migration.id}>
              <td data-label="Provider change">
                <a className="migration-change-cell" href={`/migrations/${migration.id}`}>
                  <span className="provider-monogram" aria-hidden="true">{migration.provider_name.slice(0, 2).toUpperCase()}</span>
                  <span><strong>{migration.provider_name}</strong><small>{migration.change_summary}</small></span>
                </a>
              </td>
              <td data-label="Repository"><code>{migration.repository_full_name}</code></td>
              <td data-label="Risk"><RiskPill risk={migration.risk} /></td>
              <td data-label="Effective"><time dateTime={migration.effective_at ?? undefined}>{formatDate(migration.effective_at)}</time></td>
              <td data-label="Status">
                <StatusPill status={migration.status} />
                {migration.error_code && <small className="blocked-code">{migration.error_code.replaceAll("_", " ")}</small>}
              </td>
              <td className="migration-row-action"><a href={`/migrations/${migration.id}`} aria-label={`Review ${migration.provider_name} migration`}>→</a></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MigrationInbox() {
  const [migrations, setMigrations] = useState<MigrationSummary[]>(liveApiUrl ? [] : demoMigrations);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState("all");
  const [risk, setRisk] = useState("all");
  const [view, setView] = useState<InboxView>("all");
  const [groupBy, setGroupBy] = useState<"none" | "provider" | "repository">("none");

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchMigrations(undefined, controller.signal)
      .then((page) => {
        setMigrations(page.items);
        setCursor(page.next_cursor);
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const providers = useMemo(() => [...new Set(migrations.map((item) => item.provider_name))].sort(), [migrations]);
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return migrations.filter((item) => {
      const matchesQuery = !normalized || `${item.provider_name} ${item.repository_full_name} ${item.change_summary}`.toLowerCase().includes(normalized);
      const matchesProvider = provider === "all" || item.provider_name === provider;
      const matchesRisk = risk === "all" || item.risk === risk;
      const matchesView = view === "all"
        || (view === "review" && reviewStatuses.has(item.status))
        || (view === "active" && activeStatuses.has(item.status))
        || (view === "blocked" && item.status === "blocked")
        || (view === "snoozed" && item.status === "snoozed")
        || (view === "done" && finishedStatuses.has(item.status));
      return matchesQuery && matchesProvider && matchesRisk && matchesView;
    });
  }, [migrations, provider, query, risk, view]);
  const groupedMigrations = useMemo(() => {
    if (groupBy === "none") return [];
    const groups = new Map<string, MigrationSummary[]>();
    visible.forEach((item) => {
      const key = groupBy === "provider" ? item.provider_name : item.repository_full_name;
      groups.set(key, [...(groups.get(key) || []), item]);
    });
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [groupBy, visible]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await fetchMigrations(cursor);
      setMigrations((current) => [...current, ...page.items]);
      setCursor(page.next_cursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The next page could not be loaded.");
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="dashboard-content migration-inbox-content" id="main-content">
      <div className="migration-page-heading">
        <div>
          <span className="section-kicker">Provider change operations</span>
          <h1>Migration inbox</h1>
          <p>Review affected repositories, verified patches, and draft pull requests from one queue.</p>
        </div>
        <div className="migration-heading-actions"><a className="button button-primary" href="/intelligence">Open AI briefing <span aria-hidden="true">✦</span></a><a className="button button-quiet" href="/providers">Provider health</a></div>
      </div>
      {!liveApiUrl && (
        <div className="demo-banner migration-preview-banner">
          <span className="demo-banner-icon" aria-hidden="true">◇</span>
          <div><strong>Migration workspace preview</strong><p>Representative provider changes exercise the same contracts as the live control plane.</p></div>
        </div>
      )}
      <ReadinessBanner />
      <InboxMetrics migrations={migrations} />
      <section className="migration-inbox-panel" aria-labelledby="migration-queue-title">
        <div className="inbox-toolbar">
          <div>
            <h2 id="migration-queue-title">Repository migrations</h2>
            <span>{visible.length} shown</span>
          </div>
          <label className="migration-search">
            <span aria-hidden="true">⌕</span>
            <span className="sr-only">Search migrations</span>
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Provider, repository, or change" />
          </label>
        </div>
        <div className="inbox-filters">
          <div className="inbox-view-tabs" aria-label="Migration status views">
            {(["all", "review", "active", "blocked", "snoozed", "done"] as InboxView[]).map((item) => (
              <button key={item} type="button" className={view === item ? "active" : ""} aria-pressed={view === item} onClick={() => setView(item)}>
                {item === "all" ? "All" : item === "done" ? "Decided" : item[0].toUpperCase() + item.slice(1)}
              </button>
            ))}
          </div>
          <div className="inbox-selects">
            <label><span className="sr-only">Provider</span><select value={provider} onChange={(event) => setProvider(event.target.value)}><option value="all">All providers</option>{providers.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label><span className="sr-only">Risk</span><select value={risk} onChange={(event) => setRisk(event.target.value)}><option value="all">All risk levels</option>{["critical", "high", "medium", "low"].map((item) => <option key={item} value={item}>{item[0].toUpperCase() + item.slice(1)} risk</option>)}</select></label>
            <label><span className="sr-only">Group migrations</span><select value={groupBy} onChange={(event) => setGroupBy(event.target.value as "none" | "provider" | "repository")}><option value="none">No grouping</option><option value="provider">Group by provider</option><option value="repository">Group by repository</option></select></label>
          </div>
        </div>
        {error ? (
          <div className="error-state migration-error" role="alert"><span aria-hidden="true">GH</span><div><h2>Sign in to open your migration inbox</h2><p>{error}</p>{error.includes("Sign in") && <a className="button button-primary" href={githubLoginUrlFor("/migrations")}>Continue with GitHub <span aria-hidden="true">→</span></a>}</div></div>
        ) : loading ? (
          <div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading migration work…</div>
        ) : groupBy === "none" ? <MigrationTable migrations={visible} /> : (
          <div className="migration-groups">
            {groupedMigrations.length ? groupedMigrations.map(([label, items]) => (
              <section key={label} className="migration-group">
                <div className="migration-group-heading"><strong>{label}</strong><span>{items.length} {items.length === 1 ? "migration" : "migrations"}</span></div>
                <MigrationTable migrations={items} />
              </section>
            )) : <MigrationTable migrations={[]} />}
          </div>
        )}
        {cursor && !loading && !error && (
          <div className="inbox-pagination"><button className="button button-quiet" type="button" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "Loading…" : "Load more migrations"}</button></div>
        )}
      </section>
    </div>
  );
}

function ProgressRail({ status }: { status: string }) {
  const steps = ["planning", "generating", "verifying", "ready", "pr_opened"];
  const normalized = status === "pr_opening" ? "pr_opened" : status;
  const current = Math.max(0, steps.indexOf(normalized));
  return (
    <ol className="migration-progress" aria-label="Migration progress">
      {steps.map((step, index) => (
        <li key={step} className={index < current ? "complete" : index === current ? "current" : "pending"}>
          <i aria-hidden="true">{index < current ? "✓" : index + 1}</i><span>{formatStatus(step)}</span>
        </li>
      ))}
    </ol>
  );
}

function CheckList({ evidence }: { evidence: MigrationEvidence }) {
  return (
    <div className="verification-list">
      {evidence.verification_checks.map((check) => (
        <details key={check.id} className={`verification-check check-${check.status}`}>
          <summary>
            <span aria-hidden="true">{check.status === "passed" ? "✓" : "!"}</span>
            <span><strong>{check.kind.replaceAll("_", " ")}</strong><small>{check.summary}</small></span>
            <code>{check.command || "Policy check"}</code>
            <time>{(check.duration_ms / 1000).toFixed(1)}s</time>
          </summary>
          {check.display_log && <pre>{check.display_log}</pre>}
        </details>
      ))}
    </div>
  );
}

function EvidenceTab({ evidence, change }: { evidence: MigrationEvidence; change: ChangeDetail | null }) {
  return (
    <div className="migration-detail-grid">
      <div className="migration-main-column">
        <section className="detail-card">
          <div className="detail-card-heading"><div><span className="section-kicker">Why this repository</span><h2>Impact evidence</h2></div><span className="confidence-score">{percentage(evidence.impact.confidence.score)} confidence</span></div>
          <p className="detail-lead">{evidence.impact.summary}</p>
          <div className="callsite-list">
            {evidence.impact.call_sites.map((site) => (
              <article key={site.id}>
                <span className="file-icon" aria-hidden="true">⌘</span>
                <div><code>{site.path}:{site.start_line}-{site.end_line}</code><strong>{site.symbol || site.target || "Affected call site"}</strong><p>{site.reason}</p></div>
                <span className="evidence-method">{site.detection_method}</span>
              </article>
            ))}
          </div>
          <div className="coverage-strip"><span><strong>{evidence.impact.coverage.files_considered}</strong> files analyzed</span><span><strong>{evidence.impact.coverage.parse_failures}</strong> parse failures</span><span><strong>{evidence.impact.coverage.languages.join(", ")}</strong> coverage</span></div>
        </section>
        <section className="detail-card">
          <div className="detail-card-heading"><div><span className="section-kicker">Execution plan</span><h2>{evidence.plan.summary}</h2></div></div>
          <ol className="plan-steps">{evidence.plan.steps.map((step, index) => <li key={step.id}><i>{index + 1}</i><div><strong>{step.description}</strong><span>{step.expected_paths.join(" · ")}</span></div></li>)}</ol>
        </section>
      </div>
      <aside className="migration-side-column">
        <section className="detail-card source-card">
          <span className="section-kicker">Authoritative source</span>
          <h2>{change?.provider.name || "Provider"} change</h2>
          <p>{change?.summary}</p>
          {change?.source_artifacts?.map((source) => <a key={source.id} href={source.canonical_url} target="_blank" rel="noreferrer"><span>{source.source_type.replaceAll("_", " ")}</span><b>Open source ↗</b></a>)}
          <a className="change-detail-link" href={`/changes/${change?.id}`}>View normalized change →</a>
        </section>
        <section className="detail-card">
          <span className="section-kicker">Analysis boundary</span>
          <h2>Known limitations</h2>
          <ul className="limitation-list">{evidence.impact.coverage.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      </aside>
    </div>
  );
}

function PatchTab({ evidence }: { evidence: MigrationEvidence }) {
  return (
    <div className="migration-detail-grid">
      <div className="migration-main-column">
        <section className="detail-card">
          <div className="detail-card-heading"><div><span className="section-kicker">Generated artifact</span><h2>Patch summary</h2></div><code className="digest-chip">{evidence.patch.sha256.slice(0, 12)}…</code></div>
          <p className="detail-lead">{evidence.patch.summary}</p>
          <div className="changed-file-list">{evidence.patch.files.map((file) => <article key={file.path}><span className={`file-change file-change-${file.change_type}`}>{file.change_type[0].toUpperCase()}</span><code>{file.path}</code><span>{file.change_type}</span></article>)}</div>
        </section>
        <section className="detail-card">
          <span className="section-kicker">Test changes</span><h2>Verification intent</h2>
          {evidence.tests.map((test) => <article className="test-change" key={test.path}><code>{test.path}</code><p>{test.purpose}</p><span>{test.provenance.replaceAll("_", " ")}</span></article>)}
        </section>
      </div>
      <aside className="migration-side-column"><section className="detail-card"><span className="section-kicker">Review boundary</span><h2>What was not changed</h2><p>The plan is scoped to the detected provider call sites and their directly associated tests. Unrelated repository code remains outside the patch.</p></section></aside>
    </div>
  );
}

function HistoryTab({ attempts, selected, onSelect }: { attempts: AttemptSummary[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <section className="detail-card attempt-history-card">
      <div className="detail-card-heading"><div><span className="section-kicker">Immutable record</span><h2>Attempt history</h2></div><span>{attempts.length} {attempts.length === 1 ? "attempt" : "attempts"}</span></div>
      <div className="attempt-timeline">
        {attempts.map((attempt) => (
          <button key={attempt.id} type="button" className={selected === attempt.id ? "selected" : ""} onClick={() => onSelect(attempt.id)}>
            <i aria-hidden="true" />
            <span><strong>Attempt {attempt.number}</strong><small>{formatRelative(attempt.updated_at)} · {formatStatus(attempt.status)}</small></span>
            {attempt.recommendation && <b className={`recommendation-${attempt.recommendation}`}>{attempt.recommendation}</b>}
            {attempt.developer_instructions && <p><em>Developer instruction</em>{attempt.developer_instructions}</p>}
            {attempt.error_code && <p className="attempt-error"><em>Blocked</em>{attempt.error_code.replaceAll("_", " ")}</p>}
          </button>
        ))}
      </div>
    </section>
  );
}

function ActionPanel({ migration, onUpdated }: { migration: MigrationDetail; onUpdated: (value: Partial<MigrationDetail>) => void }) {
  const [pending, setPending] = useState<PendingAction>(null);
  const [reason, setReason] = useState("");
  const [instructions, setInstructions] = useState("");
  const [snoozeUntil, setSnoozeUntil] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const currentEvidence = migration.attempts.find((attempt) => attempt.id === migration.current_attempt_id)?.evidence;
  const hasPublishableEvidence = currentEvidence?.recommendation.action === "approve"
    && currentEvidence.verification_checks.every((check) => check.status === "passed");
  const canDecide = migration.status === "ready" || migration.status === "pr_opened";
  const canGenerate = migration.status === "queued" || migration.status === "needs_revision" || (migration.status === "blocked" && !hasPublishableEvidence);
  const canPublish = migration.status === "ready" || (migration.status === "blocked" && hasPublishableEvidence);

  async function execute(action: "generate" | "publish" | RecommendationAction) {
    setBusy(action);
    setMessage("");
    const input = action === "revise"
      ? { reason, instructions }
      : action === "decline"
        ? { reason }
        : action === "snooze"
          ? { snooze_until: new Date(snoozeUntil).toISOString() }
          : {};
    try {
      if (!liveApiUrl) {
        const status: Record<string, MigrationStatus> = { generate: "planning", publish: "pr_opening", approve: "approved", revise: "needs_revision", snooze: "snoozed", decline: "declined" };
        onUpdated({ status: status[action], decision_state: action, version: migration.version + 1 });
      } else {
        const result = await runMigrationCommand(migration.id, action, migration.version, input);
        onUpdated(result as Partial<MigrationDetail>);
      }
      setMessage(action === "approve" ? "Draft pull request marked ready for normal review." : `${action[0].toUpperCase()}${action.slice(1)} accepted.`);
      setPending(null);
    } catch (problem) {
      setMessage(problem instanceof Error ? problem.message : "The action could not be completed.");
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="migration-action-card" aria-label="Migration actions">
      <div><span className="section-kicker">Developer decision</span><h2>{canDecide ? "Choose the next step" : activeStatuses.has(migration.status) ? "Automation is running" : "Available actions"}</h2></div>
      <div className="migration-actions">
        {canPublish && <button type="button" className="button button-primary" disabled={Boolean(busy)} onClick={() => execute("publish")}>{busy === "publish" ? "Publishing…" : "Open draft PR"}</button>}
        {canGenerate && <button type="button" className="button button-primary" disabled={Boolean(busy)} onClick={() => execute("generate")}>{busy === "generate" ? "Queueing…" : migration.status === "blocked" ? "Retry generation" : "Generate migration"}</button>}
        {migration.status === "pr_opened" && <button type="button" className="button button-primary" disabled={Boolean(busy)} onClick={() => execute("approve")}>{busy === "approve" ? "Approving…" : "Approve for review"}</button>}
        {canDecide && <button type="button" className="button button-quiet" onClick={() => setPending("revise")}>Request revision</button>}
        {canDecide && <button type="button" className="button button-quiet" onClick={() => setPending("snooze")}>Snooze</button>}
        {canDecide && <button type="button" className="button button-danger-quiet" onClick={() => setPending("decline")}>Decline</button>}
      </div>
      {pending && (
        <form className="action-form" onSubmit={(event) => { event.preventDefault(); execute(pending); }}>
          <div><strong>{pending === "revise" ? "Revision instructions" : pending === "snooze" ? "Snooze migration" : "Decline migration"}</strong><button type="button" aria-label="Cancel action" onClick={() => setPending(null)}>×</button></div>
          {pending !== "snooze" && <label>Reason<textarea required value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Record why this decision is needed" /></label>}
          {pending === "revise" && <label>Instructions<textarea required value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Tell the next attempt what to change" /></label>}
          {pending === "snooze" && <label>Resume after<input required type="datetime-local" value={snoozeUntil} onChange={(event) => setSnoozeUntil(event.target.value)} /></label>}
          <button className="button button-primary" type="submit" disabled={Boolean(busy)}>{busy ? "Saving…" : `Confirm ${pending}`}</button>
        </form>
      )}
      {message && <p className="action-message" role="status">{message}</p>}
      {migration.status === "approved" && <p className="action-message success-message">Approved. Delta Code never merges; the pull request stays in your normal GitHub review flow.</p>}
    </section>
  );
}

export function MigrationDetailView({ migrationId }: { migrationId: string }) {
  const [migration, setMigration] = useState<MigrationDetail | null>(liveApiUrl ? null : demoMigrationDetails[migrationId] ?? null);
  const [change, setChange] = useState<ChangeDetail | null>(liveApiUrl ? null : demoMigrationDetails[migrationId] ? demoChanges[demoMigrationDetails[migrationId].change_event_id] : null);
  const [publication, setPublication] = useState<PublicationStatus | null>(null);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [error, setError] = useState("");
  const [tab, setTab] = useState<DetailTab>("evidence");
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null);

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    const load = () => fetchMigration(migrationId, controller.signal)
      .then(async (result) => {
        setMigration(result);
        setSelectedAttemptId((current) => current || result.current_attempt_id);
        const [changeResult, publicationResult] = await Promise.all([
          fetchChange(result.change_event_id, controller.signal),
          fetchPublication(result.id, controller.signal),
        ]);
        setChange(changeResult);
        setPublication(publicationResult);
        setError("");
      })
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => setLoading(false));
    load();
    return () => controller.abort();
  }, [migrationId]);

  const migrationStatus = migration?.status;
  useEffect(() => {
    if (!liveApiUrl || !migrationStatus || !activeStatuses.has(migrationStatus)) return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      fetchMigration(migrationId, controller.signal)
        .then(setMigration)
        .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); });
    }, 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [migrationId, migrationStatus]);

  if (loading) return <div className="detail-content migration-loading"><div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading migration evidence…</div></div>;
  if (error || !migration) return <div className="detail-content"><div className="error-state" role="alert"><span aria-hidden="true">!</span><div><h1>Migration unavailable</h1><p>{error || "This migration could not be found."}</p><a className="button button-quiet" href="/migrations">Back to inbox</a></div></div></div>;

  const effectiveAttemptId = selectedAttemptId || migration.current_attempt_id || migration.attempts[0]?.id || null;
  const selectedAttempt = migration.attempts.find((attempt) => attempt.id === effectiveAttemptId) || migration.attempts[0];
  const evidence = selectedAttempt?.evidence;
  return (
    <div className="detail-content migration-detail-content" id="main-content">
      <a className="detail-back-link" href="/migrations">← Migration inbox</a>
      <header className="migration-detail-header">
        <div className="migration-detail-title">
          <div><span className="provider-monogram large" aria-hidden="true">{migration.provider_name.slice(0, 2).toUpperCase()}</span><span><small>{migration.provider_name} · <a href={`/changes/${migration.change_event_id}`}>provider change ↗</a></small><h1>{migration.change_summary}</h1></span></div>
          <p><code>{migration.repository_full_name}</code><RiskPill risk={migration.risk} /><StatusPill status={migration.status} /></p>
        </div>
        <dl className="migration-detail-meta"><div><dt>Effective</dt><dd>{formatDate(migration.effective_at || change?.effective_at)}</dd></div><div><dt>Updated</dt><dd>{formatRelative(migration.updated_at)}</dd></div><div><dt>Attempt</dt><dd>#{selectedAttempt?.number || "—"}</dd></div></dl>
      </header>
      {migration.error_code && <aside className="blocked-explanation" role="alert"><span aria-hidden="true">!</span><div><strong>Automation is blocked</strong><p>{migration.error_code.replaceAll("_", " ")}. Review the failed attempt and retry when the repository or sandbox is ready.</p></div></aside>}
      {activeStatuses.has(migration.status) && <aside className="live-progress"><span className="loading-spinner" aria-hidden="true" /><div><strong>{formatStatus(migration.status)}</strong><p>This page refreshes every five seconds while work is active.</p></div></aside>}
      <ProgressRail status={migration.status} />
      <ActionPanel migration={migration} onUpdated={(value) => setMigration((current) => current ? { ...current, ...value } : current)} />
      {publication && <aside className={`publication-strip publication-${publication.status}`}><span><strong>GitHub publication</strong><small>{formatStatus(publication.status)}</small></span><code>{publication.branch}</code>{publication.pull_url && <a href={publication.pull_url} target="_blank" rel="noreferrer">Open PR #{publication.pull_number} ↗</a>}</aside>}
      <nav className="detail-tabs" aria-label="Migration evidence sections">
        {(["evidence", "patch", "checks", "history"] as DetailTab[]).map((item) => <button key={item} type="button" className={tab === item ? "active" : ""} aria-pressed={tab === item} onClick={() => setTab(item)}>{item === "checks" ? "Verification" : item[0].toUpperCase() + item.slice(1)}</button>)}
      </nav>
      {!evidence && tab !== "history" ? (
        <section className="detail-card evidence-pending"><span aria-hidden="true">◇</span><h2>{selectedAttempt?.status === "failed" ? "This attempt did not produce evidence" : "Evidence is being assembled"}</h2><p>{selectedAttempt?.error_code ? selectedAttempt.error_code.replaceAll("_", " ") : "Planning, patch, checks, and review appear here when the attempt completes."}</p></section>
      ) : tab === "evidence" && evidence ? <EvidenceTab evidence={evidence} change={change} />
        : tab === "patch" && evidence ? <PatchTab evidence={evidence} />
          : tab === "checks" && evidence ? <section className="detail-card checks-card"><div className="detail-card-heading"><div><span className="section-kicker">Deterministic results</span><h2>Verification checks</h2></div><span>{evidence.verification_checks.filter((check) => check.status === "passed").length}/{evidence.verification_checks.length} passed</span></div><CheckList evidence={evidence} /><aside className="review-summary"><span className={`recommendation-badge recommendation-${evidence.recommendation.action}`}>{evidence.recommendation.action}</span><div><strong>Automated review</strong><p>{evidence.review.summary}</p><small>{evidence.recommendation.rationale}</small></div><b>{percentage(evidence.recommendation.confidence.score)} confidence</b></aside>{evidence.recommendation.unresolved.length > 0 && <div className="unresolved-list"><strong>Unresolved uncertainty</strong>{evidence.recommendation.unresolved.map((item) => <p key={item}>• {item}</p>)}</div>}</section>
            : <HistoryTab attempts={migration.attempts} selected={selectedAttempt?.id || null} onSelect={setSelectedAttemptId} />}
    </div>
  );
}

export function ChangeDetailView({ changeId }: { changeId: string }) {
  const [change, setChange] = useState<ChangeDetail | null>(liveApiUrl ? null : demoChanges[changeId] ?? null);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [error, setError] = useState("");
  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchChange(changeId, controller.signal).then(setChange).catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); }).finally(() => setLoading(false));
    return () => controller.abort();
  }, [changeId]);
  if (loading) return <div className="detail-content"><div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading normalized provider change…</div></div>;
  if (!change || error) return <div className="detail-content"><div className="error-state"><span>!</span><div><h1>Provider change unavailable</h1><p>{error || "This change could not be found."}</p></div></div></div>;
  const related = demoMigrations.filter((item) => item.change_event_id === change.id);
  return (
    <div className="detail-content change-detail-content" id="main-content">
      <a className="detail-back-link" href="/migrations">← Migration inbox</a>
      <header className="change-detail-header"><span className="provider-monogram large">{change.provider.name.slice(0, 2).toUpperCase()}</span><div><span className="section-kicker">Normalized provider change</span><h1>{change.summary}</h1><p>{change.provider.name}{change.provider.product ? ` · ${change.provider.product}` : ""}</p></div></header>
      <div className="change-facts"><span><small>Severity</small><RiskPill risk={change.severity} /></span><span><small>Breaking</small><strong>{change.breaking === null ? "Unknown" : change.breaking ? "Yes" : "No"}</strong></span><span><small>Effective</small><strong>{formatDate(change.effective_at)}</strong></span><span><small>Confidence</small><strong>{percentage(change.confidence.score)}</strong></span></div>
      <div className="migration-detail-grid"><div className="migration-main-column"><section className="detail-card"><span className="section-kicker">Normalized semantics</span><h2>Before and after</h2><div className="semantic-comparison"><article><span>Before</span><pre>{JSON.stringify(change.before, null, 2)}</pre></article><article><span>After</span><pre>{JSON.stringify(change.after, null, 2)}</pre></article></div></section><section className="detail-card"><span className="section-kicker">Provider claims</span><h2>Evidence provenance</h2>{change.claims?.map((claim) => <article className="change-claim" key={claim.id}><i aria-hidden="true">✓</i><div><strong>{claim.summary}</strong><small>{claim.provenance.replaceAll("_", " ")} · {claim.locator}</small></div></article>)}</section></div><aside className="migration-side-column"><section className="detail-card source-card"><span className="section-kicker">Captured sources</span><h2>Official material</h2>{change.source_artifacts?.map((source) => <a key={source.id} href={source.canonical_url} target="_blank" rel="noreferrer"><span>{source.source_type.replaceAll("_", " ")}</span><b>Open source ↗</b></a>)}</section><section className="detail-card"><span className="section-kicker">Repository fanout</span><h2>{related.length || "—"} affected in preview</h2>{related.map((migration) => <a className="related-migration" href={`/migrations/${migration.id}`} key={migration.id}><code>{migration.repository_full_name}</code><StatusPill status={migration.status} /></a>)}</section></aside></div>
    </div>
  );
}

export function ProvidersOverview() {
  const [providers, setProviders] = useState<ProviderSummary[]>(liveApiUrl ? [] : demoProviders);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [error, setError] = useState("");
  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchProviders(controller.signal)
      .then((page) => setProviders(page.items))
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);
  return (
    <div className="dashboard-content providers-content" id="main-content">
      <div className="migration-page-heading"><div><span className="section-kicker">Source operations</span><h1>Providers</h1><p>Source health, synchronization status, and repository migration coverage.</p></div></div>
      {!liveApiUrl && <div className="demo-banner"><span className="demo-banner-icon">◇</span><div><strong>Provider preview</strong><p>Live provider health comes from the authenticated control-plane feed.</p></div></div>}
      {error ? <div className="error-state migration-error" role="alert"><span aria-hidden="true">!</span><div><h2>Provider health unavailable</h2><p>{error}</p></div></div>
        : loading ? <div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading provider health…</div>
          : providers.length ? <section className="provider-grid">{providers.map((provider) => {
            const migrationCount = demoMigrations.filter((item) => item.provider_name === provider.name).length;
            return <article key={provider.id}><div><span className="provider-monogram">{provider.name.slice(0, 2).toUpperCase()}</span><span><strong>{provider.name}</strong><small className={`provider-health-${provider.status}`}>{formatStatus(provider.status)}</small></span></div><dl><span><dt>Sources</dt><dd>{provider.source_count}</dd></span><span><dt>Migrations</dt><dd>{liveApiUrl ? "—" : migrationCount}</dd></span><span><dt>Last sync</dt><dd>{provider.last_synced_at ? formatRelative(provider.last_synced_at) : "Never"}</dd></span></dl><a href="/migrations">View migrations →</a></article>;
          })}</section> : <div className="migration-empty"><span aria-hidden="true">⌁</span><h2>No providers configured</h2><p>Add an official source before Delta Code can detect repository migrations.</p></div>}
    </div>
  );
}
