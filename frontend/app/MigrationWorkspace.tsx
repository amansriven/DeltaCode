"use client";

/* eslint-disable @next/next/no-html-link-for-pages -- Native anchors keep route transitions explicit in the catch-all app shell. */
import { useEffect, useMemo, useState } from "react";
import { fetchMe, githubLoginUrlFor, liveApiUrl } from "./lib/data";
import {
  BriefMode,
  fetchWorkspaceBrief,
  generateWorkspaceBrief,
  WorkspaceBriefResponse,
} from "./lib/intelligence";
import { askDelta, DashboardChatMessage, fetchDeltaThread } from "./lib/dashboardChat";
import {
  fetchPullRequestOverview,
  fetchRecentPullRequests,
  generatePullRequestOverview,
  PullRequestOverviewResponse,
  PullRequestSummary,
} from "./lib/pullRequests";
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

function AskDeltaChat({ repositories }: { repositories: string[] }) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<DashboardChatMessage[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const hasPending = messages.some((item) => item.status === "queued" || item.status === "running");
  const scopeTooLarge = repositories.length > 10;

  useEffect(() => {
    if (!threadId || (!sending && !hasPending)) return;
    const poll = () => fetchDeltaThread(threadId)
      .then((response) => {
        setMessages(response.messages);
        if (!response.messages.some((item) => item.status === "queued" || item.status === "running")) {
          setSending(false);
        }
      })
      .catch((reason: Error) => {
        setError(reason.message);
        setSending(false);
      });
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => window.clearInterval(timer);
  }, [hasPending, sending, threadId]);

  async function sendMessage(value = message) {
    const normalized = value.trim();
    if (!normalized || repositories.length === 0 || scopeTooLarge || sending) return;
    setSending(true);
    setError("");
    setMessage("");
    try {
      const response = await askDelta(normalized, repositories, threadId);
      setThreadId(response.thread_id);
      setMessages((current) => [...current, {
        id: `optimistic-${Date.now()}`,
        role: "user",
        status: "ready",
        content: normalized,
        created_at: new Date().toISOString(),
      }, {
        id: response.message_id,
        role: "assistant",
        status: "queued",
        content: null,
        created_at: new Date().toISOString(),
      }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ask Delta could not send the question.");
      setSending(false);
    }
  }

  const suggestions = [
    "What is missing before these repositories can produce migrations?",
    "Summarize the highest-risk dashboard evidence in this scope.",
    "Which selected repository should I review first, and why?",
  ];

  return (
    <section className="ask-delta-panel">
      <header><div><span className="ask-delta-mark" aria-hidden="true">✦</span><span><small>Repository-scoped assistant</small><h2>Ask Delta</h2></span></div><span className={`ask-delta-scope ${scopeTooLarge ? "scope-warning" : ""}`}><i />{scopeTooLarge ? "Choose 10 or fewer repositories" : `${repositories.length} ${repositories.length === 1 ? "repository" : "repositories"} in scope`}</span></header>
      <div className="ask-delta-body">
        {messages.length === 0 ? <div className="ask-delta-welcome"><span>Δ</span><h3>Ask about the workspace you selected</h3><p>I can explain connected repositories, migration readiness, providers, cached PR overviews, and next actions. Unrelated questions are declined to keep context and token usage bounded.</p><div>{suggestions.map((item) => <button type="button" key={item} disabled={repositories.length === 0 || scopeTooLarge} onClick={() => sendMessage(item)}>{item}<i>↗</i></button>)}</div></div>
          : <div className="ask-delta-thread">{messages.map((item) => <article key={item.id} className={`chat-message chat-${item.role}`}><span>{item.role === "assistant" ? "Δ" : "You"}</span><div>{item.status === "queued" || item.status === "running" ? <p className="chat-thinking"><i />Reading dashboard evidence…</p> : item.status === "failed" ? <p>That answer failed safely. <code>{item.error_code || "unknown"}</code></p> : <><p>{item.answer?.answer || item.content}</p>{item.answer?.citations && item.answer.citations.length > 0 && <div className="chat-citations">{item.answer.citations.map((citation) => <a href={citation.href} key={`${citation.href}-${citation.label}`}>{citation.label}<i>→</i></a>)}</div>}{item.answer?.follow_ups && item.answer.follow_ups.length > 0 && <div className="chat-followups">{item.answer.follow_ups.map((followUp) => <button type="button" key={followUp} onClick={() => sendMessage(followUp)}>{followUp}</button>)}</div>}</>}</div></article>)}</div>}
      </div>
      {error && <p className="ask-delta-error" role="alert">{error}</p>}
      <form className="ask-delta-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}><textarea value={message} maxLength={1200} rows={2} disabled={repositories.length === 0 || scopeTooLarge || sending} placeholder={scopeTooLarge ? "Reduce the selection to 10 repositories for chat" : repositories.length ? "Ask about the selected repositories or dashboard evidence…" : "Select at least one repository above"} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} /><div><span><kbd>↵</kbd> send · <kbd>⇧↵</kbd> new line</span><small>{message.length}/1200</small><button type="submit" disabled={!message.trim() || repositories.length === 0 || scopeTooLarge || sending} aria-label="Send question">↑</button></div></form>
      <footer><span>Bounded to dashboard data</span><span>No GitHub writes</span><span>10-repository scope · 1,200-token response cap</span></footer>
    </section>
  );
}

export function WorkspaceIntelligence() {
  const [response, setResponse] = useState<WorkspaceBriefResponse | null>(null);
  const [repositories, setRepositories] = useState<string[]>([]);
  const [selectedRepositories, setSelectedRepositories] = useState<string[]>([]);
  const [mode, setMode] = useState<BriefMode>("readiness");
  const [loading, setLoading] = useState(false);
  const [repositoriesLoading, setRepositoriesLoading] = useState(Boolean(liveApiUrl));
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const responseStatus = response?.status;

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchMe(controller.signal)
      .then((user) => {
        const available = user?.accessible_repos || [];
        setRepositories(available);
        setSelectedRepositories(available.slice(0, 1));
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setRepositoriesLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!liveApiUrl || selectedRepositories.length === 0) {
      return;
    }
    const controller = new AbortController();
    fetchWorkspaceBrief(selectedRepositories, mode, controller.signal)
      .then(setResponse)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [mode, selectedRepositories]);

  useEffect(() => {
    if (!liveApiUrl || !responseStatus || !["queued", "running"].includes(responseStatus)) return;
    const timer = window.setInterval(() => {
      fetchWorkspaceBrief(selectedRepositories, mode)
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
  }, [mode, responseStatus, selectedRepositories]);

  async function generate(refresh: boolean) {
    setError("");
    setGenerating(true);
    try {
      setResponse(await generateWorkspaceBrief(selectedRepositories, mode, refresh));
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
  const modeLabels: Record<BriefMode, { label: string; description: string; icon: string }> = {
    readiness: { label: "Readiness plan", description: "Find setup gaps before the first migration.", icon: "◇" },
    repository_health: { label: "Repository health", description: "Assess coverage and operational risk.", icon: "⌂" },
    migration_portfolio: { label: "Migration portfolio", description: "Prioritize active migration work.", icon: "△" },
  };

  function toggleRepository(repository: string) {
    setLoading(true);
    setSelectedRepositories((current) => current.includes(repository)
      ? current.filter((item) => item !== repository)
      : current.length < 20 ? [...current, repository] : current);
  }
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
          <button className="button button-primary" type="button" disabled={!liveApiUrl || selectedRepositories.length === 0 || isWorking || loading || response?.configured === false || workspaceSignals === 0} onClick={() => generate(response?.status === "ready")}>
            {isWorking ? "Generating…" : response?.status === "ready" ? "Refresh briefing" : "Generate briefing"}
          </button>
        </div>
      </div>
      <section className="ai-workbench-controls">
        <div className="ai-mode-picker">
          <div><span className="section-kicker">Choose the job</span><h2>What should Delta analyze?</h2></div>
          <div className="ai-mode-grid">{(Object.entries(modeLabels) as Array<[BriefMode, { label: string; description: string; icon: string }]>).map(([value, item]) => <button type="button" key={value} className={mode === value ? "active" : ""} onClick={() => { setLoading(true); setMode(value); }}><i>{item.icon}</i><span><strong>{item.label}</strong><small>{item.description}</small></span><b aria-hidden="true">{mode === value ? "✓" : ""}</b></button>)}</div>
        </div>
        <div className="repository-scope-panel">
          <div className="repository-scope-heading"><div><span className="section-kicker">Evidence scope</span><h2>Select repositories</h2><p>Only selected repository metadata and matching dashboard evidence enter this briefing.</p></div><span><strong>{selectedRepositories.length}</strong> selected</span></div>
          {repositoriesLoading ? <div className="repository-scope-loading"><span className="loading-spinner" />Loading GitHub repositories…</div> : <div className="repository-scope-list">{repositories.map((repository) => <label key={repository} className={selectedRepositories.includes(repository) ? "selected" : ""}><input type="checkbox" checked={selectedRepositories.includes(repository)} onChange={() => toggleRepository(repository)} /><i>{repository.split("/")[1]?.slice(0, 2).toUpperCase()}</i><span>{repository}</span><b>{selectedRepositories.includes(repository) ? "✓" : "+"}</b></label>)}</div>}
          <div className="repository-scope-actions"><button type="button" onClick={() => { setLoading(true); setSelectedRepositories(repositories.slice(0, 20)); }}>Select all</button><button type="button" onClick={() => setSelectedRepositories([])}>Clear selection</button><small>Up to 20 repositories per briefing</small></div>
        </div>
      </section>
      <AskDeltaChat repositories={selectedRepositories} />
      {error && <div className="error-state intelligence-error" role="alert"><span>!</span><div><h2>AI briefing unavailable</h2><p>{error}</p>{error.includes("Sign in") && <a className="button button-primary" href={githubLoginUrlFor("/intelligence")}>Continue with GitHub</a>}</div></div>}
      {!liveApiUrl ? (
        <section className="intelligence-empty">
          <span className="ai-empty-mark" aria-hidden="true">✦</span>
          <div><span className="section-kicker">Live data required</span><h2>Connect the Delta Code backend</h2><p>Set <code>NEXT_PUBLIC_DELTA_CODE_API_URL</code> to the Railway web-service URL. AI briefing never substitutes canned model output.</p></div>
          <a className="button button-quiet" href="/docs#ai">Open setup guide →</a>
        </section>
      ) : selectedRepositories.length === 0 ? (
        <section className="intelligence-empty"><span className="ai-empty-mark" aria-hidden="true">⌂</span><div><span className="section-kicker">Repository scope required</span><h2>Choose what the model should read</h2><p>Select one or more repositories above. Delta Code will never silently sweep every connected repository.</p></div></section>
      ) : loading ? <BriefLoading /> : response?.configured === false ? (
        <section className="intelligence-empty">
          <span className="ai-empty-mark" aria-hidden="true">✦</span>
          <div><span className="section-kicker">One configuration step</span><h2>Connect the model layer</h2><p>Set <code>OPENAI_API_KEY</code> on the worker and <code>WORKSPACE_INTELLIGENCE_ENABLED=true</code> on the web service. Briefings use strict structured output and never expose the key to the browser.</p></div>
          <a className="button button-quiet" href="/docs#ai">Open setup guide →</a>
        </section>
      ) : isWorking ? <BriefLoading /> : !brief ? (
        <section className="intelligence-empty">
          <span className="ai-empty-mark" aria-hidden="true">✦</span>
          <div><span className="section-kicker">{modeLabels[mode].label}</span><h2>{selectedRepositories.length} {selectedRepositories.length === 1 ? "repository is" : "repositories are"} ready for analysis</h2><p>{modeLabels[mode].description} The model will only receive the selected scope.</p></div>
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

function PullRequestOverviewLoading() {
  return (
    <div className="pr-overview-loading" role="status">
      <span className="ai-orbit" aria-hidden="true"><i /><i /><i /></span>
      <div><strong>Reading the pull request</strong><small>Reviewing the diff, checks, commits, and discussion…</small></div>
    </div>
  );
}

export function PullRequestIntelligence() {
  const [pullRequests, setPullRequests] = useState<PullRequestSummary[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [overview, setOverview] = useState<PullRequestOverviewResponse | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchRecentPullRequests(controller.signal)
      .then((response) => {
        setPullRequests(response.items);
        if (response.items[0]) {
          setSelectedKey(`${response.items[0].repository_full_name}#${response.items[0].number}`);
        }
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const selected = useMemo(
    () => pullRequests.find((item) => `${item.repository_full_name}#${item.number}` === selectedKey) || null,
    [pullRequests, selectedKey],
  );

  useEffect(() => {
    if (!selected) {
      return;
    }
    const controller = new AbortController();
    fetchPullRequestOverview(selected.repository_full_name, selected.number, controller.signal)
      .then(setOverview)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setError(reason.message);
      })
      .finally(() => setOverviewLoading(false));
    return () => controller.abort();
  }, [selected]);

  const overviewStatus = overview?.status;
  useEffect(() => {
    if (!selected || !overviewStatus || !["queued", "running"].includes(overviewStatus)) return;
    const timer = window.setInterval(() => {
      fetchPullRequestOverview(selected.repository_full_name, selected.number)
        .then((next) => {
          setOverview(next);
          if (!["queued", "running"].includes(next.status)) setGenerating(false);
        })
        .catch((reason: Error) => {
          setError(reason.message);
          setGenerating(false);
        });
    }, 2200);
    return () => window.clearInterval(timer);
  }, [selected, overviewStatus]);

  async function analyze(refresh: boolean) {
    if (!selected) return;
    setError("");
    setGenerating(true);
    try {
      setOverview(await generatePullRequestOverview(
        selected.repository_full_name,
        selected.number,
        refresh,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The PR overview could not be generated.");
      setGenerating(false);
    }
  }

  async function refreshPullRequests() {
    setLoading(true);
    setError("");
    try {
      const response = await fetchRecentPullRequests();
      setPullRequests(response.items);
      if (!response.items.some((item) => `${item.repository_full_name}#${item.number}` === selectedKey)) {
        const first = response.items[0];
        setSelectedKey(first ? `${first.repository_full_name}#${first.number}` : "");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Pull requests could not be refreshed.");
    } finally {
      setLoading(false);
    }
  }

  function selectPullRequest(key: string) {
    setOverview(null);
    setOverviewLoading(true);
    setError("");
    setSelectedKey(key);
  }

  const normalizedQuery = query.trim().toLowerCase();
  const visiblePullRequests = pullRequests.filter((item) => !normalizedQuery
    || item.title.toLowerCase().includes(normalizedQuery)
    || item.repository_full_name.toLowerCase().includes(normalizedQuery)
    || item.author.login.toLowerCase().includes(normalizedQuery));
  const reviewedCount = pullRequests.filter((item) => item.ai_overview.status === "ready").length;
  const draftCount = pullRequests.filter((item) => item.draft).length;
  const activeCount = pullRequests.length - draftCount;
  const isWorking = generating || overviewStatus === "queued" || overviewStatus === "running";
  const result = overview?.overview;

  return (
    <div className="dashboard-content pr-intelligence-content" id="main-content">
      <div className="pr-intelligence-heading">
        <div><span className="section-kicker">GitHub review intelligence</span><h1>Pull request radar</h1><p>Scan recent work across connected repositories, then request an evidence-grounded AI overview.</p></div>
        <button className="button button-quiet" type="button" disabled={loading} onClick={refreshPullRequests}>{loading ? "Refreshing…" : "Refresh from GitHub"}</button>
      </div>
      <section className="pr-radar-metrics" aria-label="Pull request summary">
        <article><span>Open pull requests</span><strong>{pullRequests.length}</strong><small>Across connected repositories</small></article>
        <article><span>Ready for review</span><strong>{activeCount}</strong><small>Non-draft changes</small></article>
        <article><span>Drafts</span><strong>{draftCount}</strong><small>Still in progress</small></article>
        <article className="metric-ai"><span>AI overviews</span><strong>{reviewedCount}</strong><small>Generated on explicit request</small></article>
      </section>
      {error && <div className="error-state intelligence-error" role="alert"><span>!</span><div><h2>Pull request intelligence unavailable</h2><p>{error}</p>{error.includes("Sign in") && <a className="button button-primary" href={githubLoginUrlFor("/pull-requests")}>Continue with GitHub</a>}</div></div>}
      {!liveApiUrl ? (
        <section className="intelligence-empty"><span className="ai-empty-mark" aria-hidden="true">⑂</span><div><span className="section-kicker">Live data required</span><h2>Connect the Delta Code backend</h2><p>Pull request intelligence never substitutes preview repositories or fabricated reviews.</p></div></section>
      ) : loading ? <div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading recent pull requests…</div>
        : !pullRequests.length ? (
          <section className="intelligence-empty"><span className="ai-empty-mark" aria-hidden="true">⑂</span><div><span className="section-kicker">GitHub is connected</span><h2>No open pull requests found</h2><p>Open a pull request in one of the repositories selected for the Delta Code GitHub App, then refresh this page.</p></div><a className="button button-quiet" href="/settings/integrations">Review repository access →</a></section>
        ) : (
          <div className="pr-intelligence-layout">
            <aside className="pr-radar-list">
              <label className="pr-radar-search"><span aria-hidden="true">⌕</span><input type="search" value={query} placeholder="Search PRs, repositories, authors" onChange={(event) => setQuery(event.target.value)} /></label>
              <div className="pr-radar-list-heading"><span>Recently updated</span><small>{visiblePullRequests.length} shown</small></div>
              <div className="pr-radar-scroll">
                {visiblePullRequests.map((item) => {
                  const key = `${item.repository_full_name}#${item.number}`;
                  return <button type="button" key={key} className={`pr-radar-item ${selectedKey === key ? "active" : ""}`} onClick={() => selectPullRequest(key)}><span className="pr-radar-repo">{item.repository_full_name}<b>#{item.number}</b></span><strong>{item.title}</strong><small><i className={item.draft ? "draft" : "open"} />{item.draft ? "Draft" : "Open"} · @{item.author.login} · {formatRelative(item.updated_at)}</small><span className={`pr-ai-state state-${item.ai_overview.status}`}>{item.ai_overview.status === "ready" ? "✦ Reviewed" : item.ai_overview.status === "running" || item.ai_overview.status === "queued" ? "✦ Analyzing" : "AI available"}</span></button>;
                })}
              </div>
            </aside>
            <section className="pr-overview-panel">
              {selected && <header className="pr-selected-header"><div><span className="pr-selected-repo">{selected.repository_full_name} <i>#{selected.number}</i></span><h2>{selected.title}</h2><p><span className={selected.draft ? "draft" : "open"}>{selected.draft ? "Draft" : "Open"}</span> opened by @{selected.author.login} · {selected.head.ref} → {selected.base.ref}</p></div><a href={selected.html_url} target="_blank" rel="noreferrer" className="button button-quiet">Open on GitHub ↗</a></header>}
              {selected && <div className="pr-ai-consent"><span aria-hidden="true">✦</span><p><strong>User-triggered model review.</strong> Clicking generate sends this PR’s bounded diff, checks, commits, and discussion to OpenAI. Delta Code does not post comments or approve the pull request.</p><button className="button button-primary" type="button" disabled={isWorking || overviewLoading || overview?.configured === false} onClick={() => analyze(overviewStatus === "ready")}>{isWorking ? "Analyzing PR…" : overviewStatus === "ready" ? "Refresh overview" : "Generate AI overview"}</button></div>}
              {overviewLoading ? <PullRequestOverviewLoading /> : isWorking ? <PullRequestOverviewLoading /> : overviewStatus === "failed" ? (
                <div className="pr-overview-empty"><span>!</span><h3>The overview could not be generated</h3><p>Error code: <code>{overview?.error_code || "unknown"}</code>. The pull request was not changed.</p><button className="button button-quiet" type="button" onClick={() => analyze(true)}>Try again</button></div>
              ) : !result ? (
                <div className="pr-overview-empty"><span>✦</span><h3>Choose when the AI reads this pull request</h3><p>The GitHub list is live. The model call only starts after you request an overview.</p></div>
              ) : (
                <div className="pr-overview-result">
                  <section className="pr-overview-hero"><div><span className={`pr-verdict verdict-${result.verdict}`}>{result.verdict.replaceAll("_", " ")}</span><h2>{result.headline}</h2><p>{result.executive_summary}</p></div><dl><div><dt>Model</dt><dd>{overview.model || "gpt-4o"}</dd></div><div><dt>Confidence</dt><dd>{Math.round(result.confidence.score * 100)}%</dd></div><div><dt>Tokens</dt><dd>{((overview.usage?.input_tokens || 0) + (overview.usage?.output_tokens || 0)).toLocaleString()}</dd></div><div><dt>Est. cost</dt><dd>${(overview.usage?.estimated_cost_usd || 0).toFixed(4)}</dd></div></dl></section>
                  <div className="pr-overview-grid">
                    <section className="pr-overview-card"><div className="pr-card-heading"><span>01</span><div><small>What changed</small><h3>Change map</h3></div></div><ul className="pr-change-list">{result.change_summary.map((item) => <li key={item}><i>→</i><span>{item}</span></li>)}</ul></section>
                    <section className="pr-overview-card"><div className="pr-card-heading"><span>02</span><div><small>Risk model</small><h3>Signals to investigate</h3></div></div><div className="pr-risk-list">{result.risk_signals.length ? result.risk_signals.map((risk) => <article key={risk.title} className={`risk-${risk.severity}`}><span>{risk.severity}</span><strong>{risk.title}</strong><p>{risk.detail}</p><ul>{risk.evidence.map((item) => <li key={item}>{item}</li>)}</ul></article>) : <p className="pr-no-signals">No grounded risk signal was identified in the supplied context.</p>}</div></section>
                    <section className="pr-overview-card pr-focus-card"><div className="pr-card-heading"><span>03</span><div><small>Reviewer lens</small><h3>Where to focus</h3></div></div><div className="pr-focus-list">{result.review_focus.map((focus, index) => <article key={`${focus.path || focus.title}-${index}`}><i>{String(index + 1).padStart(2, "0")}</i><div>{focus.path && <code>{focus.path}</code>}<strong>{focus.title}</strong><p>{focus.detail}</p><blockquote>{focus.reviewer_question}</blockquote></div></article>)}</div></section>
                    <section className="pr-overview-card"><div className="pr-card-heading"><span>04</span><div><small>Verification</small><h3>Test posture</h3></div></div><span className={`test-posture posture-${result.test_assessment.status}`}>{result.test_assessment.status}</span><p className="test-posture-summary">{result.test_assessment.summary}</p>{result.test_assessment.missing_coverage.length > 0 && <ul className="missing-coverage-list">{result.test_assessment.missing_coverage.map((item) => <li key={item}>{item}</li>)}</ul>}</section>
                  </div>
                  <section className="pr-next-actions"><div><span className="section-kicker">Before merge</span><h3>Recommended review sequence</h3><p>{result.confidence.basis}</p></div><ol>{result.recommended_actions.map((item, index) => <li key={item}><i>{index + 1}</i><span>{item}</span></li>)}</ol></section>
                  <footer className="brief-footnote"><span>✦</span><p><strong>Advisory, not approval.</strong> This overview highlights review work from bounded GitHub evidence. It does not execute code, replace checks, post comments, or approve the pull request.</p></footer>
                </div>
              )}
            </section>
          </div>
        )}
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
