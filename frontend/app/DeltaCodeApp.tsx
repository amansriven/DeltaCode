"use client";

/* eslint-disable @next/next/no-html-link-for-pages -- Native anchors keep public and dashboard navigation explicit. */
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  ApiConnectionStatus,
  checkApiConnection,
  CurrentUser,
  fetchMe,
  githubLoginUrl,
  githubLoginUrlFor,
  githubRepositoryRefreshUrl,
  liveApiUrl,
  RepositoryAccess,
  signOut,
} from "./lib/data";
import {
  ChangeDetailView,
  MigrationDetailView,
  MigrationInbox,
  ProvidersOverview,
  PullRequestIntelligence,
  WorkspaceIntelligence,
} from "./MigrationWorkspace";

const PRODUCT_NAME = "Delta Code";
const GITHUB_INSTALL_URL = "https://github.com/apps/deltacodeapp/installations/new";
type DashboardSection = "migrations" | "intelligence" | "pull-requests" | "providers" | "repositories" | "integrations" | "settings";
type ThemePreference = "light" | "dark";

const dashboardNavigation: Array<{
  section: DashboardSection;
  label: string;
  href: string;
  icon: string;
  description: string;
}> = [
  {
    section: "migrations",
    label: "Review inbox",
    href: "/migrations",
    icon: "△",
    description: "AI migration PRs and developer decisions",
  },
  {
    section: "intelligence",
    label: "AI briefing",
    href: "/intelligence",
    icon: "✦",
    description: "OpenAI priorities and portfolio risk",
  },
  {
    section: "pull-requests",
    label: "PR intelligence",
    href: "/pull-requests",
    icon: "⑂",
    description: "Recent GitHub PRs and on-demand AI review",
  },
  {
    section: "providers",
    label: "Change sources",
    href: "/providers",
    icon: "⌁",
    description: "Official API and SDK source health",
  },
  {
    section: "repositories",
    label: "Repositories",
    href: "/repositories",
    icon: "⌂",
    description: "GitHub App repository access",
  },
  {
    section: "integrations",
    label: "Integrations",
    href: "/settings/integrations",
    icon: "⌘",
    description: "GitHub identity and permissions",
  },
  {
    section: "settings",
    label: "Settings",
    href: "/settings/account",
    icon: "◎",
    description: "Account and appearance",
  },
];

function applyTheme(theme: ThemePreference) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  window.localStorage.setItem("delta-code-theme", theme);
}

function useThemePreference() {
  const [theme, setTheme] = useState<ThemePreference>("light");

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const stored = window.localStorage.getItem("delta-code-theme");
      const initial = stored === "dark" ? "dark" : "light";
      setTheme(initial);
      applyTheme(initial);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const chooseTheme = (nextTheme: ThemePreference) => {
    setTheme(nextTheme);
    applyTheme(nextTheme);
  };

  return { theme, chooseTheme };
}

function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <a className={`wordmark ${compact ? "wordmark-compact" : ""}`} href="/" aria-label={PRODUCT_NAME}>
      <span className="delta-mark" aria-hidden="true">
        <i />
      </span>
      <span className="wordmark-name">Delta Code</span>
    </a>
  );
}

function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, chooseTheme } = useThemePreference();
  const nextTheme = theme === "light" ? "dark" : "light";
  return (
    <div className={`theme-toggle ${compact ? "theme-toggle-compact" : ""}`} aria-label="Color theme">
      <button
        type="button"
        className={`theme-switch theme-${theme}`}
        role="switch"
        aria-checked={theme === "dark"}
        aria-label={`Switch to ${nextTheme} theme`}
        onClick={() => chooseTheme(nextTheme)}
      >
        <span className="theme-switch-option" aria-hidden="true">
          ☼ {!compact && <em>Light</em>}
        </span>
        <i className="theme-switch-thumb" aria-hidden="true" />
        <span className="theme-switch-option" aria-hidden="true">
          ☾ {!compact && <em>Dark</em>}
        </span>
      </button>
    </div>
  );
}

function DemoPill() {
  return (
    <span className="demo-pill">
      <span className="demo-dot" aria-hidden="true" />
      Preview data
    </span>
  );
}

function PublicHeader() {
  return (
    <header className="public-header">
      <Wordmark />
      <nav aria-label="Public navigation">
        <a className="nav-link" href="/product">
          Product
        </a>
        <a className="nav-link" href="/how-it-works">
          Workflow
        </a>
        <a className="nav-link" href="/docs">
          Docs
        </a>
        <a className="nav-link" href="/security">
          Security
        </a>
        <a className="nav-signin" href={githubLoginUrl}>
          Sign in
        </a>
        <ThemeToggle compact />
        <a className="button button-primary button-small" href={githubLoginUrl}>
          Get started <span aria-hidden="true">→</span>
        </a>
      </nav>
    </header>
  );
}

function PublicFooter() {
  return (
    <footer className="public-footer public-footer-expanded">
      <div className="footer-brand">
        <Wordmark compact />
        <p>The AI review bot for breaking API changes.</p>
        <span>AI proposes. Evidence proves. You decide.</span>
      </div>
      <div className="footer-links">
        <div>
          <strong>Product</strong>
          <a href="/product">Overview</a>
          <a href="/how-it-works">How it works</a>
          <a href="/security">Security</a>
        </div>
        <div>
          <strong>Resources</strong>
          <a href="/docs">Documentation</a>
          <a href="/migrations">Migration inbox</a>
          <a href="https://github.com/amansriven/DeltaCode" target="_blank" rel="noreferrer">
            GitHub ↗
          </a>
        </div>
      </div>
      <div className="footer-bottom">
        <span>© 2026 Delta Code</span>
        <span className="system-status"><i /> All systems operational</span>
      </div>
    </footer>
  );
}

function userDisplayName(user: CurrentUser | null): string {
  return user?.name?.trim() || user?.login || "Account";
}

function userInitials(user: CurrentUser | null): string {
  const name = user?.name?.trim();
  if (name) {
    const parts = name.split(/\s+/).filter(Boolean);
    const first = parts[0];
    const last = parts[parts.length - 1];
    return parts.length > 1
      ? `${first[0]}${last[0]}`.toUpperCase()
      : first.slice(0, 2).toUpperCase();
  }

  const login = user?.login.replace(/[^a-z0-9]/gi, "");
  return login?.slice(0, 2).toUpperCase() || "DC";
}

function UserAvatar({
  user,
  className = "",
  size = 34,
}: {
  user: CurrentUser | null;
  className?: string;
  size?: number;
}) {
  return (
    <span className={`avatar ${className}`.trim()} aria-hidden="true">
      {user?.avatar_url ? (
        <Image src={user.avatar_url} alt="" width={size} height={size} />
      ) : (
        userInitials(user)
      )}
    </span>
  );
}

function AppHeader({ active }: { active: DashboardSection }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiConnectionStatus>(
    liveApiUrl ? "checking" : "preview",
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandQuery, setCommandQuery] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetchMe(controller.signal).then(setUser).catch(() => setUser(null));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    checkApiConnection(controller.signal)
      .then(setApiStatus)
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") setApiStatus("unavailable");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen((open) => !open);
      }
      if (event.key === "Escape") {
        setCommandOpen(false);
        setMenuOpen(false);
        setMobileOpen(false);
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  const displayName = userDisplayName(user);
  const visibleCommands = dashboardNavigation.filter((item) => {
    const normalized = commandQuery.trim().toLowerCase();
    return (
      !normalized ||
      item.label.toLowerCase().includes(normalized) ||
      item.description.toLowerCase().includes(normalized)
    );
  });

  return (
    <>
      <aside className={`app-sidebar ${mobileOpen ? "mobile-open" : ""}`}>
        <div className="sidebar-brand">
          <Wordmark />
          <button
            type="button"
            className="mobile-sidebar-close"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          >
            ×
          </button>
        </div>
        <button
          type="button"
          className="command-trigger"
          onClick={() => setCommandOpen(true)}
        >
          <span aria-hidden="true">⌕</span>
          <span>Search workspace</span>
          <kbd>⌘K</kbd>
        </button>
        <nav className="sidebar-nav" aria-label="Dashboard navigation">
          <span>Workspace</span>
          {dashboardNavigation.map((item) => (
            <a
              key={item.section}
              className={active === item.section ? "active" : ""}
              href={item.href}
              aria-current={active === item.section ? "page" : undefined}
            >
              <i aria-hidden="true">{item.icon}</i>
              <span>{item.label}</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span
            className={`api-indicator api-indicator-${apiStatus}`}
            title={apiStatus === "upgrade-required" ? "The API is online but missing required routes." : undefined}
          >
            <i />
            {{
              preview: "Preview workspace",
              checking: "Checking API",
              connected: "API connected",
              "upgrade-required": "API update required",
              unavailable: "API unavailable",
            }[apiStatus]}
          </span>
          <ThemeToggle />
          <div className="sidebar-account">
            <button
              type="button"
              className="sidebar-account-button"
              aria-label={user ? `Signed in as ${displayName}` : "Account menu"}
              aria-haspopup="true"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <UserAvatar user={user} />
              <span>
                <strong>{user ? displayName : "Sign in"}</strong>
                <small>
                  {user
                    ? `@${user.login} · ${user.accessible_repos.length} repositories`
                    : "Connect your GitHub account"}
                </small>
              </span>
              <i aria-hidden="true">⌄</i>
            </button>
            {menuOpen && (
              <div className="account-dropdown sidebar-account-dropdown" role="menu">
                <span className="account-dropdown-name">
                  {user ? displayName : "Not signed in"}
                </span>
                {user ? (
                  <>
                    <a role="menuitem" href="/settings/account">Account settings</a>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        signOut().finally(() => {
                          window.location.href = "/";
                        });
                      }}
                    >
                      Sign out
                    </button>
                  </>
                ) : (
                  <a role="menuitem" href={githubLoginUrl}>Sign in with GitHub</a>
                )}
              </div>
            )}
          </div>
        </div>
      </aside>
      {mobileOpen && (
        <button
          type="button"
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <header className="app-header">
        <div className="app-header-inner">
          <button
            type="button"
            className="mobile-nav-trigger"
            aria-label="Open navigation"
            onClick={() => setMobileOpen(true)}
          >
            <span />
            <span />
          </button>
          <div className="app-header-context">
            <span>Delta Code</span>
            <i aria-hidden="true">/</i>
            <strong>{dashboardNavigation.find((item) => item.section === active)?.label}</strong>
          </div>
          <div className="account-cluster">
            {!liveApiUrl && <DemoPill />}
            <a
              className="topbar-avatar"
              href="/settings/account"
              aria-label={user ? `Account settings for ${displayName}` : "Account settings"}
            >
              <UserAvatar user={user} size={32} />
            </a>
          </div>
        </div>
      </header>
      {commandOpen && (
        <div
          className="command-palette-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) setCommandOpen(false);
          }}
        >
          <section className="command-palette" role="dialog" aria-modal="true" aria-label="Search Delta Code">
            <label>
              <span aria-hidden="true">⌕</span>
              <input
                autoFocus
                type="search"
                value={commandQuery}
                placeholder="Where do you want to go?"
                onChange={(event) => setCommandQuery(event.target.value)}
              />
              <kbd>esc</kbd>
            </label>
            <div className="command-results">
              <span>Navigate</span>
              {visibleCommands.map((item) => (
                <a key={item.section} href={item.href}>
                  <i aria-hidden="true">{item.icon}</i>
                  <span>
                    <strong>{item.label}</strong>
                    <small>{item.description}</small>
                  </span>
                  <b aria-hidden="true">↗</b>
                </a>
              ))}
              {visibleCommands.length === 0 && <p>No matching destination.</p>}
            </div>
          </section>
        </div>
      )}
    </>
  );
}

function MigrationInboxPage() {
  return (
    <main className="dashboard-page">
      <AppHeader active="migrations" />
      <MigrationInbox />
    </main>
  );
}

function MigrationPage({ migrationId }: { migrationId: string }) {
  return (
    <main className="dashboard-page">
      <AppHeader active="migrations" />
      <MigrationDetailView migrationId={migrationId} />
    </main>
  );
}

function ChangePage({ changeId }: { changeId: string }) {
  return (
    <main className="dashboard-page">
      <AppHeader active="migrations" />
      <ChangeDetailView changeId={changeId} />
    </main>
  );
}

function ProvidersPage() {
  return (
    <main className="dashboard-page">
      <AppHeader active="providers" />
      <ProvidersOverview />
    </main>
  );
}

function IntelligencePage() {
  return (
    <main className="dashboard-page">
      <AppHeader active="intelligence" />
      <WorkspaceIntelligence />
    </main>
  );
}

function PullRequestsPage() {
  return (
    <main className="dashboard-page">
      <AppHeader active="pull-requests" />
      <PullRequestIntelligence />
    </main>
  );
}

function repoParts(repo: string) {
  const [owner, name] = repo.split("/");
  return { owner: owner || "repository", name: name || repo };
}

function repositoriesForUser(user: CurrentUser | null): RepositoryAccess[] {
  if (!user) return [];
  if (user.repositories?.length) return user.repositories;
  return user.accessible_repos.map((full_name) => ({
    full_name,
    private: null,
    visibility: "unknown",
  }));
}

function useWorkspaceData() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));
  const [error, setError] = useState("");

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchMe(controller.signal)
      .then((identity) => {
        setUser(identity);
        setError(identity ? "" : "Sign in with GitHub to view this workspace.");
      })
      .catch((reason: Error) => {
        if (reason.name !== "AbortError") {
          setError(reason.message || "The workspace could not be loaded.");
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  return {
    user,
    repositories: repositoriesForUser(user),
    loading,
    error,
  };
}

function ExperienceShell({ children }: { children: React.ReactNode }) {
  const shellRef = useRef<HTMLDivElement>(null);

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const x = (event.clientX / window.innerWidth) * 100;
    const y = (event.clientY / window.innerHeight) * 100;
    shellRef.current?.style.setProperty("--experience-x", `${x}%`);
    shellRef.current?.style.setProperty("--experience-y", `${y}%`);
  }

  return (
    <div
      ref={shellRef}
      className="experience-shell"
      onPointerMove={handlePointerMove}
      onPointerLeave={() => {
        shellRef.current?.style.setProperty("--experience-x", "76%");
        shellRef.current?.style.setProperty("--experience-y", "18%");
      }}
    >
      <div className="experience-backdrop" aria-hidden="true">
        <span className="experience-spotlight" />
        <span className="experience-orb experience-orb-one" />
        <span className="experience-orb experience-orb-two" />
        <span className="experience-orb experience-orb-three" />
        <span className="experience-grid" />
        <span className="data-lane data-lane-one"><i /></span>
        <span className="data-lane data-lane-two"><i /></span>
        <span className="data-lane data-lane-three"><i /></span>
      </div>
      <div className="experience-content">{children}</div>
    </div>
  );
}

function InteractiveLandingShell({ children }: { children: React.ReactNode }) {
  const shellRef = useRef<HTMLDivElement>(null);

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * 100;
    const y = ((event.clientY - bounds.top) / bounds.height) * 100;
    shellRef.current?.style.setProperty("--pointer-x", `${x}%`);
    shellRef.current?.style.setProperty("--pointer-y", `${y}%`);
  }

  return (
    <div
      ref={shellRef}
      className="landing-shell"
      onPointerMove={handlePointerMove}
      onPointerLeave={() => {
        shellRef.current?.style.setProperty("--pointer-x", "72%");
        shellRef.current?.style.setProperty("--pointer-y", "24%");
      }}
    >
      <div className="cursor-aura" aria-hidden="true" />
      <div className="aurora aurora-a" aria-hidden="true" />
      <div className="aurora aurora-b" aria-hidden="true" />
      <div className="mesh-grid" aria-hidden="true" />
      <div className="api-flow-field" aria-hidden="true">
        <svg viewBox="0 0 1440 780" preserveAspectRatio="xMidYMid slice">
          <defs>
            <linearGradient id="delta-flow-primary" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="currentColor" stopOpacity="0" />
              <stop offset=".45" stopColor="currentColor" stopOpacity=".72" />
              <stop offset="1" stopColor="currentColor" stopOpacity=".08" />
            </linearGradient>
            <linearGradient id="delta-flow-change" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#22a9e8" stopOpacity=".16" />
              <stop offset=".6" stopColor="#1769ff" stopOpacity=".68" />
              <stop offset="1" stopColor="#e34962" stopOpacity=".22" />
            </linearGradient>
          </defs>
          <path className="flow-path flow-path-muted" d="M-80 180 C180 180 242 124 444 124 S720 210 916 174 1190 112 1520 112" />
          <path className="flow-path flow-path-muted" d="M-40 620 C210 620 294 532 482 532 S780 652 1010 594 1238 500 1510 526" />
          <path className="flow-path flow-path-base" d="M310 388 C486 388 534 310 686 310 S876 244 1070 244 1274 296 1490 270" />
          <path className="flow-path flow-path-head" d="M310 388 C486 388 534 462 686 462 S882 536 1072 536 1274 482 1490 516" />
          <path className="flow-path flow-path-spine" d="M-50 388 C86 388 180 388 310 388" />
          <circle className="flow-hub" cx="310" cy="388" r="7" />
          <circle className="flow-node node-base" cx="686" cy="310" r="5" />
          <circle className="flow-node node-head" cx="686" cy="462" r="5" />
          <circle className="flow-node node-change" cx="1072" cy="536" r="6" />
        </svg>
        <span className="flow-packet flow-packet-one" />
        <span className="flow-packet flow-packet-two" />
        <span className="flow-packet flow-packet-three" />
        <span className="flow-chip flow-chip-request"><b>GET</b> /v1/items</span>
        <span className="flow-chip flow-chip-base"><i /> base · 200</span>
        <span className="flow-chip flow-chip-head"><i /> head · 422</span>
        <span className="flow-difference">behavior changed</span>
      </div>
      <div className="constellation" aria-hidden="true">
        <i /><i /><i /><i /><i /><i />
        <span /><span /><span />
      </div>
      {children}
    </div>
  );
}

function ReviewBotPreview() {
  return (
    <div className="review-bot-preview" aria-label="Delta Code migration pull request preview">
      <div className="review-bot-topbar">
        <span className="review-bot-repo"><i aria-hidden="true">GH</i> acme/checkout</span>
        <span className="draft-pr-badge">Draft PR #184</span>
      </div>
      <div className="review-bot-title">
        <div className="bot-avatar" aria-hidden="true">Δ</div>
        <div>
          <small>DELTA CODE BOT · 2 MINUTES AGO</small>
          <h2>Migrate checkout requests to PaymentMethod</h2>
          <p>Stripe API 2026-08-01 removes the legacy <code>source</code> field.</p>
        </div>
      </div>
      <div className="review-bot-trace">
        <div className="trace-item trace-verified">
          <span>01</span>
          <div><strong>Provider change verified</strong><small>Official migration guide · artifact captured</small></div>
          <b>verified</b>
        </div>
        <div className="trace-item trace-verified">
          <span>02</span>
          <div><strong>3 affected call sites found</strong><small>src/billing/checkout.ts · tests/checkout.test.ts</small></div>
          <b>mapped</b>
        </div>
        <div className="trace-item trace-ai">
          <span>03</span>
          <div><strong>Patch and tests generated</strong><small>Bounded repository context · strict structured output</small></div>
          <b>GPT-4o</b>
        </div>
        <div className="trace-item trace-verified">
          <span>04</span>
          <div><strong>14 verification checks passed</strong><small>lint · type-check · unit tests · sandbox destroyed</small></div>
          <b>passed</b>
        </div>
      </div>
      <div className="review-bot-files">
        <span><i>M</i><code>src/billing/checkout.ts</code><small>+12 −8</small></span>
        <span><i>A</i><code>tests/payment-method.test.ts</code><small>+34</small></span>
      </div>
      <div className="review-bot-decision">
        <span><i aria-hidden="true">✓</i><strong>Ready for developer review</strong></span>
        <div><button type="button">Request revision</button><button type="button">Review PR ↗</button></div>
      </div>
    </div>
  );
}

function LandingPage() {
  return (
    <main className="public-page bot-public-page">
      <InteractiveLandingShell>
        <PublicHeader />
        <section className="hero bot-hero">
          <div className="hero-copy bot-hero-copy">
            <div className="eyebrow">
              <span />
              AI migration review bot
              <b>GPT-4o</b>
            </div>
            <h1>
              The AI review bot
              <br />
              <em>for breaking API changes.</em>
            </h1>
            <p>
              Delta Code watches official provider changes, finds the code they affect,
              uses GPT-4o to write and review the migration, verifies the exact patch,
              and opens a draft pull request for your team.
            </p>
            <div className="hero-actions">
              <a className="button button-primary button-large" href={githubLoginUrl}>
                Connect GitHub <span aria-hidden="true">→</span>
              </a>
              <a className="button button-quiet button-large" href="/migrations">
                Open the review inbox
              </a>
            </div>
            <div className="hero-proof">
              <span><i>✓</i> Official-source provenance</span>
              <span><i>✓</i> Verified before PR</span>
              <span><i>✓</i> Human-controlled merge</span>
            </div>
          </div>
          <div className="hero-visual bot-hero-visual">
            <div className="floating-chip chip-one"><i /> Provider change detected</div>
            <div className="floating-chip chip-two">✦ AI review complete</div>
            <ReviewBotPreview />
            <div className="visual-glow" aria-hidden="true" />
          </div>
        </section>
        <div className="trusted-strip">
          <span>Built for the code review loop</span>
          <div>
            <b>OPENAI</b>
            <b>GITHUB</b>
            <b>OPENAPI</b>
            <b>PYTHON + TS</b>
            <b>SANDBOXED</b>
          </div>
        </div>
      </InteractiveLandingShell>

      <section className="positioning-section">
        <div className="positioning-statement">
          <span className="section-kicker">A new category of dependency automation</span>
          <h2>Dependabot finds version bumps.<br /><em>Delta Code ships API migrations.</em></h2>
        </div>
        <div className="positioning-copy">
          <p>External APIs change outside your dependency graph. Delta Code connects the provider announcement to the exact repository, call sites, patch, tests, verification evidence, and draft PR.</p>
          <a href="/product">See the complete product →</a>
        </div>
        <div className="positioning-metrics">
          <span><strong>Official</strong><small>source provenance</small></span>
          <span><strong>Repository-specific</strong><small>impact and patches</small></span>
          <span><strong>Verified</strong><small>before developer review</small></span>
        </div>
      </section>

      <section className="bot-workflow-section">
        <div className="section-heading centered-heading">
          <span className="section-kicker">From provider change to pull request</span>
          <h2>One bot. The complete migration review loop.</h2>
          <p>Every step leaves an inspectable artifact, so the final recommendation can be reviewed instead of merely trusted.</p>
        </div>
        <div className="bot-workflow-grid">
          <article><span>01</span><i>⌁</i><h3>Watch the source</h3><p>Capture official specs, releases, guides, and SDK changes with immutable provenance.</p><code>provider.change</code></article>
          <article><span>02</span><i>◎</i><h3>Trace impact</h3><p>Map the change to dependencies, symbols, and concrete call sites in connected repositories.</p><code>3 call sites</code></article>
          <article className="workflow-ai"><span>03</span><i>✦</i><h3>Generate with AI</h3><p>GPT-4o proposes a minimal migration and tests from bounded repository context.</p><code>strict JSON</code></article>
          <article><span>04</span><i>✓</i><h3>Verify the patch</h3><p>Run allowed commands in an isolated sandbox and preserve every check result.</p><code>14 / 14 passed</code></article>
          <article><span>05</span><i>↗</i><h3>Open a draft PR</h3><p>Publish the exact verified patch, evidence, uncertainty, and review recommendation.</p><code>human decision</code></article>
        </div>
      </section>

      <section className="ai-system-section">
        <div className="ai-system-copy">
          <span className="section-kicker">AI with an evidence boundary</span>
          <h2>Give the model judgment.<br />Keep proof deterministic.</h2>
          <p>GPT-4o interprets provider material, understands repository context, writes the patch and tests, and reviews its work. Delta Code independently owns source hashes, call-site evidence, patch policy, sandbox checks, and Git state.</p>
          <div className="ai-boundary-list">
            <span><i>AI</i><strong>Plans, patches, tests, and review explanations</strong></span>
            <span><i>✓</i><strong>Provenance, execution, checks, and exact Git artifacts</strong></span>
          </div>
        </div>
        <div className="model-trace-card">
          <div><span>AI REVIEW TRACE</span><b>gpt-4o</b></div>
          <ol>
            <li><span>context</span><code>provider + repository + call sites</code><b>bounded</b></li>
            <li><span>proposal</span><code>plan + patch + tests</code><b>validated</b></li>
            <li><span>sandbox</span><code>lint + typecheck + test</code><b className="trace-pass">passed</b></li>
            <li><span>review</span><code>evidence-grounded recommendation</code><b>approve</b></li>
          </ol>
          <div className="model-trace-footer"><span>tools disabled</span><span>store: false</span><span>budget limited</span></div>
        </div>
      </section>

      <section className="final-cta bot-final-cta">
        <div className="cta-orb" aria-hidden="true" />
        <span className="section-kicker">The next API migration can arrive as a PR</span>
        <h2>Let the bot do the migration.<br />Keep the decision yours.</h2>
        <p>Connect GitHub and turn provider changes into verified, reviewable work before they become emergency upgrades.</p>
        <div>
          <a className="button button-primary button-large" href={githubLoginUrl}>Get started with GitHub <span>→</span></a>
          <a className="button button-quiet button-large" href="/docs">Read the architecture</a>
        </div>
      </section>
      <PublicFooter />
    </main>
  );
}

function PublicPageHero({
  kicker,
  title,
  description,
  children,
}: {
  kicker: string;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <section className="subpage-hero">
      <span className="section-kicker">{kicker}</span>
      <h1>{title}</h1>
      <p>{description}</p>
      {children}
    </section>
  );
}

function ProductPage() {
  return (
    <main className="public-page light-public-page bot-subpage">
      <PublicHeader />
      <PublicPageHero
        kicker="The API migration review bot"
        title="One review bot from provider change to verified PR."
        description="Delta Code combines official-source monitoring, repository impact analysis, GPT-4o migration intelligence, sandbox verification, and draft-PR publishing in one developer-controlled workflow."
      >
        <div className="subpage-actions">
          <a className="button button-primary button-large" href={githubLoginUrl}>Get started with GitHub →</a>
          <a className="button button-quiet button-large" href="/how-it-works">Explore the workflow</a>
        </div>
      </PublicPageHero>
      <section className="product-showcase">
        <div className="showcase-copy">
          <span className="section-kicker">A complete review artifact</span>
          <h2>From official change to a PR your team can evaluate.</h2>
          <p>Every migration keeps its provider source, affected call sites, AI-generated plan, exact patch, tests, sandbox checks, model review, uncertainty, and developer decision together.</p>
          <ul className="check-list">
            <li><i>✓</i> Official-source provenance</li>
            <li><i>✓</i> Repository and call-site evidence</li>
            <li><i>✓</i> GPT-4o patch generation and review</li>
            <li><i>✓</i> Verified draft PRs with immutable attempts</li>
          </ul>
        </div>
        <ReviewBotPreview />
      </section>
      <section className="feature-matrix">
        {[
          ["⌁", "Provider intelligence", "Captures official specs, releases, guides, and SDK changes with immutable provenance."],
          ["◎", "Repository impact", "Connects a normalized change to concrete dependencies, symbols, and call sites."],
          ["✦", "AI migration generation", "GPT-4o proposes bounded plans, edits, tests, and allowed verification commands."],
          ["✓", "Sandbox verification", "Runs the exact patch in an isolated boundary and records every deterministic check."],
          ["↗", "Draft PR publishing", "Commits the verified artifact, opens an owned draft PR, and publishes evidence."],
          ["◇", "Human review controls", "Approve, revise, snooze, decline, retry, and publish without automatic merging."],
        ].map(([icon, title, copy]) => (
          <article key={title}>
            <span>{icon}</span><h3>{title}</h3><p>{copy}</p>
          </article>
        ))}
      </section>
      <PublicFooter />
    </main>
  );
}

function WorkflowPage() {
  return (
    <main className="public-page light-public-page bot-subpage">
      <PublicHeader />
      <PublicPageHero
        kicker="How Delta Code works"
        title="A provider change enters. A verified draft PR comes out."
        description="Delta Code turns external change management into a durable review workflow, with GPT-4o handling repository-specific judgment and deterministic systems preserving proof."
      />
      <section className="timeline-section">
        {[
          ["01", "An official provider source changes", "Delta Code captures and fingerprints the artifact, then normalizes it into a provider-independent change.", "source.captured"],
          ["02", "Connected repositories are assessed", "Dependency inventory and language analyzers locate supported call sites and expose coverage limits.", "impact.assessed"],
          ["03", "GPT-4o proposes the migration", "Bounded provider, repository, and call-site context produces a schema-validated plan, patch, tests, and commands.", "ai.proposal"],
          ["04", "Patch policy constrains the output", "Only supplied files, expected hashes, known call sites, and approved command arrays can advance.", "patch.validated"],
          ["05", "The exact patch runs in a sandbox", "Build, lint, type-check, test, and behavioral checks produce deterministic evidence.", "sandbox.verified"],
          ["06", "GPT-4o reviews against the evidence", "The model identifies grounded findings and recommends approve, revise, snooze, or decline.", "ai.review"],
          ["07", "A draft pull request reaches the developer", "Delta Code publishes the verified patch, source links, checks, uncertainty, and immutable attempt history.", "pull_request.draft"],
        ].map(([number, title, copy, code]) => (
          <article key={number}>
            <span className="timeline-number">{number}</span>
            <div><h2>{title}</h2><p>{copy}</p></div>
            <code>{code}</code>
          </article>
        ))}
      </section>
      <section className="principle-callout">
        <span>Our core principle</span>
        <blockquote>“AI proposes. Evidence proves. You decide.”</blockquote>
        <p>GPT-4o supplies the judgment required to migrate unfamiliar repository code. Deterministic systems establish what source was captured, what code was affected, what patch ran, and which checks passed.</p>
      </section>
      <PublicFooter />
    </main>
  );
}

function DocsPage() {
  return (
    <main className="public-page light-public-page bot-subpage">
      <PublicHeader />
      <div className="docs-layout">
        <aside className="docs-sidebar">
          <span>Documentation</span>
          <a className="active" href="#quickstart">Quickstart</a>
          <a href="#architecture">Architecture</a>
          <a href="#local-development">Local development</a>
          <a href="#api">API reference</a>
          <a href="#ai">AI assistance</a>
        </aside>
        <article className="docs-content">
          <span className="section-kicker">Delta Code docs</span>
          <h1 id="quickstart">Run the AI migration review bot.</h1>
          <p className="docs-lede">Start the control plane, worker, migration inbox, GPT-4o intelligence, and optional sandbox executor locally—or connect the hosted product to a GitHub App installation.</p>
          <div className="docs-note"><b>Prerequisites</b><span>Python 3.12+, Node.js 22.13+, Docker, Git, and Make.</span></div>
          <h2>Local quickstart</h2>
          <p>Install dependencies and start PostgreSQL:</p>
          <pre><code>{`make setup\nmake db-up\nmake db-schema`}</code></pre>
          <p>Then run the API, worker, and frontend in separate terminals:</p>
          <pre><code>{`make api\nmake worker\nmake frontend-dev LIVE_API_URL=http://localhost:8000`}</code></pre>
          <h2 id="architecture">Architecture</h2>
          <div className="architecture-row">
            <span>Provider source</span><i>→</i><span>Impact analysis</span><i>→</i><span>GPT-4o</span><i>→</i><span>Sandbox</span><i>→</i><span>Draft PR</span>
          </div>
          <h2 id="local-development">Local URLs</h2>
          <table className="docs-table"><tbody>
            <tr><th>Dashboard</th><td><code>http://localhost:3000</code></td></tr>
            <tr><th>Backend API</th><td><code>http://localhost:8000</code></td></tr>
            <tr><th>Interactive API docs</th><td><code>http://localhost:8000/docs</code></td></tr>
            <tr><th>Health endpoint</th><td><code>http://localhost:8000/health</code></td></tr>
          </tbody></table>
          <h2 id="api">Core API</h2>
          <div className="endpoint-list">
            <span><b>GET</b><code>/auth/me</code><small>Current GitHub identity and repositories</small></span>
            <span><b>GET</b><code>/migrations</code><small>Workspace-scoped migration review inbox</small></span>
            <span><b>POST</b><code>/migrations/&#123;id&#125;/generate</code><small>Queue a bounded AI migration attempt</small></span>
            <span><b>GET</b><code>/providers</code><small>Configured provider sources and sync health</small></span>
            <span><b>GET</b><code>/migrations/&#123;id&#125;</code><small>Migration evidence, patch, checks, and history</small></span>
            <span><b>POST</b><code>/migrations/&#123;id&#125;/publish</code><small>Publish an approved migration as a draft pull request</small></span>
          </div>
          <h2 id="ai">AI assistance</h2>
          <p>Set <code>OPENAI_API_KEY</code> on the Railway worker and <code>WORKSPACE_INTELLIGENCE_ENABLED=true</code> on the Railway web service. Vercel only needs the backend API URL; the key must never be exposed through a browser or <code>NEXT_PUBLIC_</code> variable.</p>
          <pre><code>{`OPENAI_API_KEY=your-server-side-key\nOPENAI_MODEL=gpt-4o\nWORKSPACE_INTELLIGENCE_ENABLED=true\nLLM_DAILY_BUDGET_USD=1.00\nLLM_TOTAL_BUDGET_USD=9.00`}</code></pre>
          <p>Model requests use strict structured output, no model tools, bounded context and output, <code>store: false</code>, retry limits, and request-level cost limits. Repository and provider text is treated as untrusted data.</p>
        </article>
      </div>
      <PublicFooter />
    </main>
  );
}

function SecurityPage() {
  return (
    <main className="public-page light-public-page bot-subpage">
      <PublicHeader />
      <PublicPageHero
        kicker="Security and model boundaries"
        title="Give the bot enough context to migrate. Never enough authority to decide alone."
        description="Delta Code separates GitHub access, model interpretation, trusted patch policy, sandbox execution, deterministic evidence, and developer approval into explicit boundaries."
      />
      <section className="security-grid">
        <article className="security-primary">
          <span className="card-icon">◈</span>
          <h2>Least privilege from source capture to pull request</h2>
          <p>Delta Code can only access repositories selected during GitHub App installation, and generated patches remain drafts until a developer reviews them.</p>
        </article>
        <article><span>01</span><h3>Prompt-injection resistance</h3><p>Provider, repository, and developer text is untrusted data. Model tools are disabled and outputs must match strict schemas.</p></article>
        <article><span>02</span><h3>Bounded model context</h3><p>Only known source evidence, call sites, selected files, and explicit instructions enter each GPT-4o request.</p></article>
        <article><span>03</span><h3>Fail-closed sandbox</h3><p>Repository-controlled commands run in a separate executor with deny-by-default networking and explicit enablement.</p></article>
        <article><span>04</span><h3>Human-controlled Git writes</h3><p>The publisher owns its branch and exact patch, opens a draft PR, and never auto-merges the migration.</p></article>
      </section>
      <section className="security-boundary">
        <div><span className="section-kicker">Clear trust boundaries</span><h2>Interpretation, proof, and authority remain separate.</h2></div>
        <div className="boundary-cards">
          <article><b>GPT-4o</b><p>Plans, edits, tests, explanations, and evidence-grounded review recommendations.</p></article>
          <span>≠</span>
          <article><b>Deterministic evidence</b><p>Source hashes, call sites, patch validation, sandbox checks, Git artifacts, and audit history.</p></article>
          <span>≠</span>
          <article><b>Developer decision</b><p>Approve, revise, snooze, decline, publish, and ultimately merge.</p></article>
        </div>
      </section>
      <PublicFooter />
    </main>
  );
}

function LoginPage() {
  return (
    <main className="auth-page">
      <div className="auth-brand">
        <Wordmark />
        <a className="back-link" href="/">
          ← Back to home
        </a>
      </div>
      <section className="auth-card">
        <div className="auth-icon" aria-hidden="true">
          GH
        </div>
        <span className="section-kicker">Developer access</span>
        <h1>Open your migration review inbox</h1>
        <p className="auth-intro">
          Sign in with GitHub to review provider changes, AI-generated migrations,
          verification evidence, and draft pull requests for repositories where
          Delta Code is installed.
        </p>
        {!liveApiUrl && (
          <div className="preview-notice" role="status">
            <span aria-hidden="true">i</span>
            Connect the Delta Code API to enable GitHub sign-in in this preview.
          </div>
        )}
        <a className="button button-primary button-full" href={githubLoginUrl}>
          <span className="github-button-mark" aria-hidden="true">
            GH
          </span>
          Continue with GitHub
        </a>
        <div className="auth-divider">
          <span />
          <em>or</em>
          <span />
        </div>
        <a className="button button-quiet button-full" href="/migrations">
          Enter demo workspace
        </a>
        <p className="auth-fineprint">
          GitHub identity and repository installation are separate. Delta Code only
          receives access to repositories you explicitly select.
        </p>
      </section>
      <p className="auth-footer">Authentication is handled by GitHub and a secure server-side session.</p>
    </main>
  );
}

function OnboardingPage() {
  const [user, setUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchMe(controller.signal).then(setUser).catch(() => setUser(null));
    return () => controller.abort();
  }, []);

  return (
    <main className="onboarding-page">
      <header className="onboarding-header">
        <Wordmark />
        <span className="setup-progress">Setup · 2 of 3</span>
      </header>
      <section className="onboarding-content">
        <div className="onboarding-copy">
          <span className="section-kicker">Connect a repository</span>
          <h1>Put the API migration bot in your pull requests.</h1>
          <p>
            Install the GitHub App on the repositories you want verified.
            Delta Code will connect provider changes to affected code, prepare
            verified migrations, and open developer-controlled draft PRs.
          </p>
        </div>
        <div className="setup-grid">
          <article className={`setup-step ${user ? "complete" : ""}`}>
            <span className="step-icon">{user ? "✓" : "01"}</span>
            <div>
              <small>Step 1</small>
              <h2>GitHub identity</h2>
              <p>
                {user
                  ? `Connected as ${userDisplayName(user)} (@${user.login})`
                  : "Sign in to connect your GitHub identity"}
              </p>
            </div>
            <span className="step-state">{user ? "Complete" : "Required"}</span>
          </article>
          <article className="setup-step current">
            <span className="step-icon">02</span>
            <div>
              <small>Step 2</small>
              <h2>Install Delta Code</h2>
              <p>Choose an account and the repositories Delta Code can access.</p>
              <button className="button button-primary" type="button" disabled>
                GitHub App connection coming next
              </button>
            </div>
            <span className="step-state">Current</span>
          </article>
          <article className="setup-step">
            <span className="step-icon">03</span>
            <div>
              <small>Step 3</small>
              <h2>Verify access</h2>
              <p>We’ll confirm your installation and start monitoring configured provider sources.</p>
            </div>
            <span className="step-state">Next</span>
          </article>
        </div>
        <div className="onboarding-actions">
          <a className="button button-primary" href="/migrations">
            Continue with demo workspace →
          </a>
          <a className="text-link" href={githubLoginUrl}>
            Use another account
          </a>
        </div>
      </section>
    </main>
  );
}

function RepositoryVisibilityBadge({ repository }: { repository: RepositoryAccess }) {
  const visibility =
    repository.visibility === "internal"
      ? "Internal"
      : repository.private === true
        ? "Private"
        : repository.private === false
          ? "Public"
          : "Unknown";
  return (
    <span
      className={`repository-visibility ${
        repository.visibility === "internal"
          ? "repository-internal"
          : repository.private === true
            ? "repository-private"
            : repository.private === false
              ? "repository-public"
              : ""
      }`}
    >
      <i aria-hidden="true">
        {repository.visibility === "internal" ? "◈" : repository.private ? "⌑" : "○"}
      </i>
      {visibility}
    </span>
  );
}

function RepositoriesPage() {
  const { repositories, loading, error } = useWorkspaceData();
  const [query, setQuery] = useState("");
  const [visibility, setVisibility] = useState<"all" | "public" | "private" | "internal">("all");
  const normalizedQuery = query.trim().toLowerCase();
  const visibleRepositories = repositories.filter((repository) => {
    const matchesQuery =
      !normalizedQuery || repository.full_name.toLowerCase().includes(normalizedQuery);
    const repositoryVisibility =
      repository.visibility === "internal"
        ? "internal"
        : repository.private === true
          ? "private"
          : repository.private === false
            ? "public"
            : "all";
    return matchesQuery && (visibility === "all" || repositoryVisibility === visibility);
  });

  return (
    <main className="dashboard-page">
      <AppHeader active="repositories" />
      <div className="dashboard-content repositories-content">
        <div className="page-heading">
          <div>
            <span className="section-kicker">GitHub App access</span>
            <h1>Repositories</h1>
            <p>Manage the repositories Delta Code can analyze and migrate.</p>
          </div>
          <a className="button button-primary" href={GITHUB_INSTALL_URL} target="_blank" rel="noreferrer">
            Choose repositories ↗
          </a>
        </div>
        <aside className="access-explainer">
          <span aria-hidden="true">⌁</span>
          <div>
            <strong>Access stays repository-scoped.</strong>
            <p>GitHub controls this list. Delta Code can only analyze repositories selected in the GitHub App installation.</p>
          </div>
          <a href={githubRepositoryRefreshUrl}>Refresh access</a>
        </aside>
        <section className="repository-directory" aria-labelledby="repository-directory-title">
          <div className="repository-directory-toolbar">
            <div>
              <h2 id="repository-directory-title">Repository directory</h2>
              <span>{visibleRepositories.length} of {repositories.length}</span>
            </div>
            <div className="repository-filters">
              <label className="search-field">
                <span aria-hidden="true">⌕</span>
                <span className="sr-only">Search repositories</span>
                <input type="search" value={query} placeholder="Search repositories" onChange={(event) => setQuery(event.target.value)} />
              </label>
              <div className="visibility-filter" aria-label="Repository visibility">
                {(["all", "private", "public", "internal"] as const).map((option) => (
                  <button key={option} type="button" className={visibility === option ? "active" : ""} aria-pressed={visibility === option} onClick={() => setVisibility(option)}>
                    {option[0].toUpperCase() + option.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {error ? (
            <div className="error-state" role="alert">
              <span aria-hidden="true">!</span>
              <div>
                <h2>Repository access unavailable</h2>
                <p>{error}</p>
                {error.includes("Sign in") && <a className="button button-primary" href={githubLoginUrlFor("/repositories")}>Continue with GitHub</a>}
              </div>
            </div>
          ) : loading ? (
            <div className="loading-state" role="status"><span className="loading-spinner" aria-hidden="true" />Loading repository access…</div>
          ) : visibleRepositories.length ? (
            <div className="repository-directory-list repository-access-list">
              <div className="repository-directory-head" aria-hidden="true">
                <span>Repository</span><span>Visibility</span><span>Access</span><span />
              </div>
              {visibleRepositories.map((repository) => {
                const parts = repoParts(repository.full_name);
                return (
                  <article key={repository.full_name}>
                    <div className="repo-cell">
                      <span className="repo-mark" aria-hidden="true">{parts.name.slice(0, 2).toUpperCase()}</span>
                      <span><strong>{parts.name}</strong><small>{parts.owner}</small></span>
                    </div>
                    <RepositoryVisibilityBadge repository={repository} />
                    <span className="integration-state connected"><i /> Authorized</span>
                    <a className="repository-open" href={`https://github.com/${repository.full_name}`} target="_blank" rel="noreferrer" aria-label={`Open ${repository.full_name} on GitHub`}>↗</a>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="empty-state">
              <span className="empty-icon" aria-hidden="true">⌂</span>
              <h2>No repositories match this view</h2>
              <p>Clear the search or choose another visibility filter.</p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function IntegrationsPage() {
  const { user, repositories, loading, error } = useWorkspaceData();
  const privateCount = repositories.filter((repository) => repository.private === true).length;
  const publicCount = repositories.filter((repository) => repository.private === false).length;
  const internalCount = repositories.filter((repository) => repository.visibility === "internal").length;
  const permissionState = (permission: string, allowed: string[]): boolean | null => {
    if (repositories.length === 0 || repositories.some((repository) => !repository.permissions)) {
      return null;
    }
    return repositories.every((repository) => allowed.includes(repository.permissions?.[permission] ?? ""));
  };
  const permissionRows = [
    { key: "contents", label: "Read repository contents", detail: "Inspect README, manifests, configuration, and code", state: permissionState("contents", ["read", "write"]) },
    { key: "pull_requests", label: "Read pull requests", detail: "Analyze recent pull request metadata and changes", state: permissionState("pull_requests", ["read", "write"]) },
    { key: "checks", label: "Write checks", detail: "Publish verification evidence to GitHub", state: permissionState("checks", ["write"]) },
    { key: "metadata", label: "Read metadata", detail: "Display repository identity and visibility", state: permissionState("metadata", ["read"]) },
  ];
  const contentsState = permissionRows[0].state;

  return (
    <main className="dashboard-page">
      <AppHeader active="integrations" />
      <div className="dashboard-content integrations-content">
        <div className="page-heading">
          <div>
            <span className="section-kicker">Connected services</span>
            <h1>Integrations</h1>
            <p>Manage the GitHub identity and repository installation used by Delta Code.</p>
          </div>
        </div>
        {error ? (
          <div className="error-state" role="alert">
            <span aria-hidden="true">!</span>
            <div>
              <h2>GitHub connection unavailable</h2>
              <p>{error}</p>
              {error.includes("Sign in") && (
                <a className="button button-primary" href={githubLoginUrlFor("/settings/integrations")}>Continue with GitHub</a>
              )}
            </div>
          </div>
        ) : loading ? (
          <div className="loading-state" role="status">
            <span className="loading-spinner" aria-hidden="true" />
            Loading GitHub connection…
          </div>
        ) : (
          <section className="github-integration">
            <div className="github-integration-heading">
              <span className="integration-logo" aria-hidden="true">GH</span>
              <div>
                <span className="integration-state connected"><i /> Connected</span>
                <h2>GitHub</h2>
                <p>OAuth identity and GitHub App repository access</p>
              </div>
              <a href="https://github.com/settings/installations" target="_blank" rel="noreferrer">
                Open GitHub settings ↗
              </a>
            </div>
            <div className="github-integration-grid">
              <section>
                <span className="integration-section-label">Connected account</span>
                <div className="identity-row integration-identity">
                  <UserAvatar user={user} className="settings-avatar" size={46} />
                  <span>
                    <strong>{user ? userDisplayName(user) : "Sign in"}</strong>
                    <a
                      href={user ? `https://github.com/${user.login}` : githubLoginUrlFor("/settings/integrations")}
                      target={user ? "_blank" : undefined}
                      rel={user ? "noreferrer" : undefined}
                    >
                      {user ? `@${user.login} ↗` : "Sign in with GitHub"}
                    </a>
                  </span>
                </div>
              </section>
              <section>
                <span className="integration-section-label">GitHub App installation</span>
                <div className="installation-summary">
                  <span className="delta-mark delta-mark-small" aria-hidden="true"><i /></span>
                  <span>
                    <strong>Delta Code GitHub App</strong>
                    <small>{repositories.length} accessible repositories</small>
                  </span>
                  <b className={contentsState === false ? "limited" : undefined}>{contentsState === false ? "Source limited" : "Active"}</b>
                </div>
                <div className="visibility-summary">
                  <span><strong>{privateCount}</strong><small>Private</small></span>
                  <span><strong>{publicCount}</strong><small>Public</small></span>
                  <span><strong>{internalCount}</strong><small>Internal</small></span>
                </div>
              </section>
            </div>
            {contentsState === false && (
              <aside className="integration-permission-alert" role="alert">
                <span aria-hidden="true">!</span>
                <div><strong>Repository listing works, but source analysis is blocked</strong><p>The GitHub App is installed for these repositories, but it is missing Contents: Read. Ask Delta can still use metadata and pull requests; it cannot inspect README files or code.</p></div>
                <a href="https://github.com/settings/apps/deltacodeapp/permissions" target="_blank" rel="noreferrer">Update App permission ↗</a>
              </aside>
            )}
            <section className="permissions-panel">
              <div>
                <span className="integration-section-label">Permissions</span>
                <h3>Only what verification requires</h3>
              </div>
              <div className="permission-list">
                {permissionRows.map((permission) => (
                  <span className={permission.state === false ? "permission-missing" : permission.state === null ? "permission-unknown" : undefined} key={permission.key}>
                    <i>{permission.state === true ? "✓" : permission.state === false ? "!" : "?"}</i>
                    <strong>{permission.label}</strong>
                    <small>{permission.state === null ? "Refresh repository access to verify" : permission.detail}</small>
                  </span>
                ))}
              </div>
            </section>
            <div className="integration-actions">
              <a className="button button-primary" href={GITHUB_INSTALL_URL} target="_blank" rel="noreferrer">
                Choose repositories on GitHub ↗
              </a>
              <a className="button button-quiet" href={githubRepositoryRefreshUrl}>
                Refresh repository access
              </a>
            </div>
          </section>
        )}
        <aside className="repository-security-note integration-privacy-note">
          <span aria-hidden="true">⌁</span>
          <div>
            <strong>Session and repository privacy</strong>
            <p>
              Authentication stays server-side. Delta Code uses short-lived installation tokens
              for selected repositories and does not expose GitHub credentials to the browser.
            </p>
          </div>
        </aside>
      </div>
    </main>
  );
}

function AppearancePanel() {
  const { theme, chooseTheme } = useThemePreference();
  return (
    <section className="appearance-panel">
      <div>
        <span className="section-kicker">Appearance</span>
        <h2>Choose your workspace theme</h2>
        <p>Light mode is the default. Your preference is saved on this device.</p>
      </div>
      <div className="appearance-options">
        {(["light", "dark"] as const).map((option) => (
          <button
            key={option}
            type="button"
            className={theme === option ? "active" : ""}
            aria-pressed={theme === option}
            onClick={() => chooseTheme(option)}
          >
            <span className={`theme-preview theme-preview-${option}`}>
              <i />
              <b />
              <em />
            </span>
            <strong>{option === "light" ? "Light" : "Dark"}</strong>
            <small>{option === "light" ? "Bright, calm, and focused" : "Low-glare for late sessions"}</small>
            <i aria-hidden="true">{theme === option ? "✓" : ""}</i>
          </button>
        ))}
      </div>
    </section>
  );
}

function SettingsPage({ tab }: { tab: "account" | "repositories" }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(Boolean(liveApiUrl));

  useEffect(() => {
    if (!liveApiUrl) return;
    const controller = new AbortController();
    fetchMe(controller.signal)
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const repositories: RepositoryAccess[] = user
    ? user.repositories?.length
      ? user.repositories
      : user.accessible_repos.map((full_name) => ({
          full_name,
          private: null,
          visibility: "unknown",
        }))
    : [];
  const privateRepositoryCount = repositories.filter(
    (repository) => repository.private === true,
  ).length;

  return (
    <main className="dashboard-page">
      <AppHeader active="settings" />
      <div className="dashboard-content settings-content">
        <div className="page-heading">
          <div>
            <span className="section-kicker">Workspace settings</span>
            <h1>Settings</h1>
            <p>Manage the identity and repository access Delta Code uses.</p>
          </div>
        </div>
        <nav className="settings-tabs" aria-label="Settings sections">
          <a className={tab === "account" ? "active" : ""} href="/settings/account">Account</a>
          <a className={tab === "repositories" ? "active" : ""} href="/settings/integrations">Integrations</a>
        </nav>
        {loading ? (
          <div className="loading-state settings-loading" role="status">
            <span className="loading-spinner" aria-hidden="true" />
            Loading settings…
          </div>
        ) : !user ? (
          <div className="error-state settings-signin">
            <span aria-hidden="true">!</span>
            <div>
              <h2>Sign in required</h2>
              <p>Sign in with GitHub to manage your account and repository access.</p>
              <a className="button button-primary" href={githubLoginUrlFor(`/settings/${tab}`)}>Continue with GitHub</a>
            </div>
          </div>
        ) : tab === "account" ? (
          <div className="settings-grid">
          <section className="integration-card">
            <div className="integration-logo" aria-hidden="true">
              GH
            </div>
            <div className="integration-main">
              <div>
                <span className="integration-state connected">
                  <i /> Connected
                </span>
                <h2>GitHub account</h2>
                <p>Your GitHub identity controls access to this dashboard.</p>
              </div>
              <div className="identity-row">
                <UserAvatar user={user} className="settings-avatar" size={38} />
                <span>
                  <strong>{userDisplayName(user)}</strong>
                  <a href={`https://github.com/${user.login}`} target="_blank" rel="noreferrer">
                    @{user.login} · View GitHub profile ↗
                  </a>
                </span>
              </div>
            </div>
          </section>
          <AppearancePanel />
          </div>
        ) : (
          <div className="settings-grid">
          <section className="integration-card">
            <div className="integration-logo delta-logo" aria-hidden="true">
              Δ
            </div>
            <div className="integration-main">
              <div>
                <span className="integration-state connected">
                  <i /> {repositories.length} connected
                </span>
                <h2>Delta Code GitHub App</h2>
                <p>
                  The app receives PR events, checks out selected repositories,
                  and publishes verification evidence.
                </p>
              </div>
              <div className="repository-list">
                {repositories.length ? repositories.map((repository) => {
                  const parts = repoParts(repository.full_name);
                  const visibility =
                    repository.visibility === "internal"
                      ? "Internal"
                      : repository.private === true
                      ? "Private"
                      : repository.private === false
                        ? "Public"
                        : "Visibility unknown";
                  return (
                    <div className="repository-access-row" key={repository.full_name}>
                      <i>{parts.name.slice(0, 2).toUpperCase()}</i>
                      <span className="repository-access-name">{repository.full_name}</span>
                      <span
                        className={`repository-visibility ${
                          repository.visibility === "internal"
                            ? ""
                            : repository.private === true
                            ? "repository-private"
                            : repository.private === false
                              ? "repository-public"
                              : ""
                        }`}
                      >
                        {visibility}
                      </span>
                    </div>
                  );
                }) : (
                  <div className="repository-empty">
                    <strong>No repositories connected yet</strong>
                    <small>Choose repositories in the GitHub App installation flow.</small>
                  </div>
                )}
              </div>
            </div>
          </section>
          <section className="install-card">
            <div>
              <span className="section-kicker">
                {privateRepositoryCount
                  ? `${privateRepositoryCount} private ${
                      privateRepositoryCount === 1 ? "repository" : "repositories"
                    } authorized`
                  : "Repository access"}
              </span>
              <h2>Connect public or private repositories</h2>
              <p>
                GitHub controls the repository picker. For a private repository,
                its owner or organization administrator must grant the Delta Code
                GitHub App access.
              </p>
            </div>
            <div className="repository-access-actions">
              <a
                className="button button-primary"
                href={GITHUB_INSTALL_URL}
                target="_blank"
                rel="noreferrer"
              >
                Choose repositories on GitHub ↗
              </a>
              <a className="button button-quiet" href={githubRepositoryRefreshUrl}>
                Refresh repository access
              </a>
            </div>
          </section>
          <aside className="repository-security-note">
            <span aria-hidden="true">⌁</span>
            <div>
              <strong>GitHub access stays repository-scoped.</strong>
              <p>
                Delta Code uses a short-lived GitHub App installation token only
                while checking out a selected revision. Removing a repository
                from the GitHub installation prevents future access.
              </p>
            </div>
          </aside>
          </div>
        )}
      </div>
    </main>
  );
}

export default function DeltaCodeApp({ route }: { route: string[] }) {
  const path = route.join("/");
  let page: React.ReactNode = null;

  if (!path) page = <LandingPage />;
  else if (path === "product") page = <ProductPage />;
  else if (path === "how-it-works") page = <WorkflowPage />;
  else if (path === "docs") page = <DocsPage />;
  else if (path === "security") page = <SecurityPage />;
  else if (path === "login") page = <LoginPage />;
  else if (path === "onboarding") page = <OnboardingPage />;
  else if (path === "migrations") page = <MigrationInboxPage />;
  else if (path === "intelligence") page = <IntelligencePage />;
  else if (path === "pull-requests") page = <PullRequestsPage />;
  else if (path === "providers") page = <ProvidersPage />;
  else if (path === "repositories") page = <RepositoriesPage />;
  if (path.startsWith("migrations/")) {
    page = <MigrationPage migrationId={path.split("/")[1]} />;
  } else if (path.startsWith("changes/")) {
    page = <ChangePage changeId={path.split("/")[1]} />;
  } else if (path === "settings" || path === "settings/account") {
    page = <SettingsPage tab="account" />;
  } else if (path === "settings/integrations") {
    page = <IntegrationsPage />;
  } else if (!page) {
    page = (
      <main className="not-found-state standalone">
        <Wordmark />
        <span aria-hidden="true">404</span>
        <h1>That page isn’t part of Delta Code.</h1>
        <p>The route may have moved or never existed.</p>
        <a className="button button-primary" href="/">
          Return home
        </a>
      </main>
    );
  }

  return <ExperienceShell>{page}</ExperienceShell>;
}
