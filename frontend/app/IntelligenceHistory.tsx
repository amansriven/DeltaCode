"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "./IntelligenceHistory.module.css";
import {
  fetchIntelligenceHistory,
  fetchIntelligenceHistoryDetail,
  IntelligenceHistoryDetail,
  IntelligenceHistoryItem,
  IntelligenceHistoryKind,
  IntelligenceUsage,
} from "./lib/intelligenceHistory";

const kindLabels: Record<IntelligenceHistoryKind, string> = {
  all: "All activity",
  chat: "Ask Delta",
  briefing: "Briefings",
  pull_request: "PR overviews",
};

const kindIcons = { chat: "Δ", briefing: "✦", pull_request: "⑂" } as const;

function relativeTime(value: string) {
  const minutes = Math.max(1, Math.round((Date.now() - new Date(value).getTime()) / 60_000));
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1_440) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1_440)}d ago`;
}

function Usage({ usage, model }: { usage: IntelligenceUsage; model: string | null }) {
  return <footer className={styles.usage}><span>{model || "Model pending"}</span><span>{(usage.input_tokens + usage.output_tokens).toLocaleString()} tokens</span><span>${usage.estimated_cost_usd.toFixed(4)}</span></footer>;
}

function HistoryDetailView({ item, detail }: { item: IntelligenceHistoryItem; detail: IntelligenceHistoryDetail }) {
  if (detail.kind === "chat") {
    return <><header className={styles.detailHeader}><div><span className={styles.badge}>Ask Delta thread</span><h2>{item.title}</h2><p>{item.repository_full_names.join(" · ")}</p></div><span>{detail.messages.length} messages</span></header><div className={styles.messages}>{detail.messages.map((message) => <article className={styles.message} key={message.id}><span>{message.role === "assistant" ? "DELTA" : "YOU"}</span><p>{message.answer?.answer || message.content || (message.status === "failed" ? `Failed: ${message.error_code}` : "Response pending…")}</p></article>)}</div><Usage usage={item.usage} model={item.model} /></>;
  }
  if (detail.kind === "briefing") {
    return <><header className={styles.detailHeader}><div><span className={styles.badge}>{detail.scope.mode?.replaceAll("_", " ") || "Repository briefing"}</span><h2>{detail.brief?.headline || item.title}</h2><p>{(detail.scope.repository_full_names || []).join(" · ")}</p></div><span>{new Date(detail.updated_at).toLocaleDateString()}</span></header>{detail.brief ? <div className={styles.brief}><p>{detail.brief.executive_summary}</p><div className={styles.attention}>{detail.brief.attention_summary}</div><div className={styles.cards}>{detail.brief.priorities.map((priority) => <article className={styles.card} key={priority.title}><small>{priority.urgency}</small><h3>{priority.title}</h3><p>{priority.reason}</p><ul>{priority.evidence.map((evidence) => <li key={evidence}>{evidence}</li>)}</ul></article>)}</div></div> : <div className={styles.loading}>This briefing did not complete.</div>}<Usage usage={detail.usage} model={detail.model} /></>;
  }
  return <><header className={styles.detailHeader}><div><span className={styles.badge}>PR #{detail.pull_number}</span><h2>{detail.overview?.headline || detail.snapshot?.title || item.title}</h2><p>{detail.repository_full_name} · <code>{detail.head_sha?.slice(0, 8) || "commit pending"}</code></p></div>{detail.snapshot?.html_url && <a href={detail.snapshot.html_url} target="_blank" rel="noreferrer">GitHub ↗</a>}</header>{detail.overview ? <div className={styles.brief}><p>{detail.overview.executive_summary}</p><div className={styles.attention}>{detail.overview.verdict.replaceAll("_", " ")} · {Math.round(detail.overview.confidence.score * 100)}% confidence</div><div className={styles.cards}><article className={styles.card}><small>Change map</small><ul>{detail.overview.change_summary.map((change) => <li key={change}>{change}</li>)}</ul></article><article className={styles.card}><small>Recommended actions</small><ul>{detail.overview.recommended_actions.map((action) => <li key={action}>{action}</li>)}</ul></article></div></div> : <div className={styles.loading}>This overview did not complete.</div>}<Usage usage={detail.usage} model={detail.model} /></>;
}

export function IntelligenceHistory() {
  const [kind, setKind] = useState<IntelligenceHistoryKind>("all");
  const [query, setQuery] = useState("");
  const [repository, setRepository] = useState("");
  const [items, setItems] = useState<IntelligenceHistoryItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<IntelligenceHistoryItem | null>(null);
  const [detail, setDetail] = useState<IntelligenceHistoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      fetchIntelligenceHistory(kind, query, repository, null, controller.signal)
        .then((response) => {
          setItems(response.items);
          setNextCursor(response.next_cursor);
          setDetail(null);
          setDetailLoading(true);
          setSelected((current) => response.items.find((item) => item.id === current?.id) || response.items[0] || null);
        })
        .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
        .finally(() => setLoading(false));
    }, 180);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [kind, query, repository]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    fetchIntelligenceHistoryDetail(selected, controller.signal)
      .then(setDetail)
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => setDetailLoading(false));
    return () => controller.abort();
  }, [selected]);

  const repositories = useMemo(() => Array.from(new Set(items.flatMap((item) => item.repository_full_names))).sort(), [items]);
  const totalTokens = items.reduce((total, item) => total + item.usage.input_tokens + item.usage.output_tokens, 0);

  async function loadMore() {
    if (!nextCursor) return;
    const response = await fetchIntelligenceHistory(kind, query, repository, nextCursor);
    setItems((current) => [...current, ...response.items]);
    setNextCursor(response.next_cursor);
  }

  function selectItem(item: IntelligenceHistoryItem) {
    setDetail(null);
    setDetailLoading(true);
    setSelected(item);
  }

  return <div className={styles.shell} id="main-content"><header className={styles.heading}><div><small>Durable intelligence record</small><h1>Intelligence history</h1><p>Return to previous Ask Delta conversations, repository briefings, and pull-request overviews—with the original scope, model usage, and repository state attached.</p></div><div className={styles.totals}><span><strong>{items.length}</strong><small>records shown</small></span><span><strong>{totalTokens.toLocaleString()}</strong><small>tokens shown</small></span></div></header><section className={styles.toolbar}><div className={styles.tabs}>{(Object.keys(kindLabels) as IntelligenceHistoryKind[]).map((value) => <button type="button" key={value} data-active={kind === value} onClick={() => { setLoading(true); setKind(value); }}>{kindLabels[value]}</button>)}</div><label className={styles.search}><span>⌕</span><input type="search" value={query} placeholder="Search titles, answers, repositories" onChange={(event) => { setLoading(true); setQuery(event.target.value); }} /></label><select className={styles.repository} value={repository} onChange={(event) => { setLoading(true); setRepository(event.target.value); }}><option value="">All repositories</option>{repositories.map((name) => <option value={name} key={name}>{name}</option>)}</select></section>{error ? <div className={styles.error}>{error}</div> : <section className={styles.layout}><aside className={styles.timeline}><div className={styles.timelineHeader}><span>Most recently updated</span><span>{items.length} items</span></div>{loading ? <div className={styles.loading}>Loading intelligence history…</div> : <div className={styles.list}>{items.map((item) => <button className={styles.item} type="button" data-active={selected?.id === item.id && selected.kind === item.kind} key={`${item.kind}-${item.id}`} onClick={() => selectItem(item)}><span className={styles.kind}>{kindIcons[item.kind]}</span><span><strong>{item.title}</strong><p>{item.summary}</p><span className={styles.meta}><span>{relativeTime(item.updated_at)}</span><span>{item.status}</span>{item.kind === "pull_request" && <span>{item.metadata.is_current ? "Current version" : "Historical version"}</span>}{item.repository_full_names.slice(0, 2).map((name) => <span key={name}>{name}</span>)}</span></span></button>)}{nextCursor && <button className={styles.loadMore} type="button" onClick={loadMore}>Load older activity</button>}</div>}</aside><article className={styles.detail}>{!selected ? <div className={styles.detailEmpty}><i>⌁</i><h2>No history yet</h2><p>Generated conversations and briefings will appear here.</p></div> : detailLoading || !detail ? <div className={styles.loading}>Opening the original intelligence record…</div> : <HistoryDetailView item={selected} detail={detail} />}</article></section>}</div>;
}
