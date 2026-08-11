"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import {
  Search,
  Play,
  Pause,
  RefreshCw,
  AlertTriangle,
  Copy,
  Check,
  ScrollText,
} from "lucide-react";
import type { LogEntry } from "@/lib/types";
import { ALL_LEVELS, LEVEL_COLOR, LEVEL_SOFT, parseLevel } from "@/lib/logs";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as LogEntry[];
};

const listFetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return (await res.json()) as string[];
};

const RANGES = [
  { label: "15m", mins: 15 },
  { label: "1h", mins: 60 },
  { label: "6h", mins: 360 },
  { label: "24h", mins: 1440 },
  { label: "7d", mins: 10080 },
];

function formatTime(ts: number, minutes: number) {
  const d = new Date(ts);
  return minutes > 1440 ? d.toLocaleString() : d.toLocaleTimeString();
}

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Highlighted({ text, query }: { text: string; query: string }) {
  const q = query.trim();
  if (!q) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegExp(q)})`, "gi"));
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === q.toLowerCase() ? (
          <mark key={i} className="rounded-sm px-0.5" style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

const selectClass = "rounded-[var(--radius-control)] px-3 py-2 text-sm text-color-text outline-none transition-colors";
const selectStyle = { border: "1px solid var(--border)", background: "var(--canvas)" } as const;

function LogRow({ entry, query, minutes }: { entry: LogEntry; query: string; minutes: number }) {
  const level = parseLevel(entry.line);
  const color = level ? LEVEL_COLOR[level] : "var(--neutral)";
  const soft = level ? LEVEL_SOFT[level] : "var(--neutral-soft)";
  const [copied, setCopied] = useState(false);

  async function copyLine() {
    try {
      await navigator.clipboard.writeText(entry.line);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard unavailable, ignore
    }
  }

  return (
    <div
      className="group grid grid-cols-[80px_60px_1fr_28px] items-start gap-3 border-b px-4 py-2 text-[13px] last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[92px_74px_120px_120px_1fr_28px]"
      style={{ borderColor: "var(--border-soft)" }}
    >
      <div className="stat-figure whitespace-nowrap pt-0.5 text-[11px] text-text-faint">{formatTime(entry.ts, minutes)}</div>
      <div className="pt-0.5">
        <span
          className="inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[10px] font-semibold"
          style={{ color, background: soft }}
        >
          {level ?? "LOG"}
        </span>
      </div>
      <div className="hidden truncate pt-0.5 text-text-dim sm:block">{entry.host ?? "—"}</div>
      <div className="hidden truncate pt-0.5 text-text-faint sm:block">{entry.source ?? "—"}</div>
      <div
        className="whitespace-pre-wrap break-all text-[12.5px] leading-relaxed text-color-text"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        <Highlighted text={entry.line} query={query} />
      </div>
      <button
        onClick={copyLine}
        className="hidden h-6 w-6 items-center justify-center rounded text-text-faint opacity-0 transition-opacity hover:bg-[var(--border-soft)] group-hover:opacity-100 sm:flex"
        aria-label="Copy log line"
      >
        {copied ? <Check className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

export default function LogViewer() {
  const [host, setHost] = useState("all");
  const [source, setSource] = useState("all");
  const [level, setLevel] = useState("all");
  const [minutes, setMinutes] = useState(15);
  const [live, setLive] = useState(true);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput), 400);
    return () => clearTimeout(t);
  }, [qInput]);

  const { data: hosts } = useSWR<string[]>("/api/logs/hosts", listFetcher, { refreshInterval: 30000 });
  const { data: sources } = useSWR<string[]>("/api/logs/sources", listFetcher, { refreshInterval: 30000 });

  const params = new URLSearchParams();
  params.set("host", host);
  params.set("source", source);
  params.set("level", level);
  if (q) params.set("q", q);
  params.set("minutes", String(minutes));
  params.set("limit", "500");
  const key = `/api/logs?${params.toString()}`;

  const { data: entries, error, isLoading, isValidating, mutate } = useSWR<LogEntry[]>(key, fetcher, {
    refreshInterval: live ? 5000 : 0,
    keepPreviousData: true,
  });

  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <ScrollText className="h-4.5 w-4.5 text-text-dim" strokeWidth={1.75} />
          </div>
          <div>
            <div className="eyebrow">Monitoring</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Logs</h1>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setLive((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium transition-colors"
            style={{
              border: "1px solid var(--border)",
              background: live ? "var(--accent-soft)" : "transparent",
              color: live ? "var(--accent)" : "var(--text-dim)",
            }}
          >
            {live ? (
              <span className="status-dot" style={{ background: "var(--accent)" }} />
            ) : (
              <Play className="h-3.5 w-3.5" strokeWidth={2} />
            )}
            {live ? "Live" : "Paused"}
            {live ? <Pause className="h-3.5 w-3.5" strokeWidth={2} /> : null}
          </button>
          <button
            onClick={() => mutate()}
            aria-label="Refresh logs"
            className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
          </button>
        </div>
      </div>

      <div className="panel grid gap-3 p-4">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" strokeWidth={2} />
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search log messages…"
            className="w-full rounded-[var(--radius-control)] py-2.5 pl-9 pr-3.5 text-sm text-color-text outline-none transition-colors"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <select value={host} onChange={(e) => setHost(e.target.value)} className={selectClass} style={selectStyle}>
            <option value="all">All hosts</option>
            {hosts?.map((h) => (
              <option key={h} value={h}>{h}</option>
            ))}
          </select>

          <select value={source} onChange={(e) => setSource(e.target.value)} className={selectClass} style={selectStyle}>
            <option value="all">All sources</option>
            {sources?.map((s) => (
              <option key={s} value={s}>{s === "system" ? "system (syslog)" : s}</option>
            ))}
          </select>

          <select value={level} onChange={(e) => setLevel(e.target.value)} className={selectClass} style={selectStyle}>
            <option value="all">All levels</option>
            {ALL_LEVELS.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>

          <div className="ml-auto inline-flex rounded-[var(--radius-control)] p-0.5" style={{ border: "1px solid var(--border)" }}>
            {RANGES.map((r) => (
              <button
                key={r.mins}
                onClick={() => setMinutes(r.mins)}
                className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
                style={{
                  background: minutes === r.mins ? "var(--accent)" : "transparent",
                  color: minutes === r.mins ? "#fff" : "var(--text-dim)",
                }}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between px-0.5 text-xs text-text-faint">
          <span>
            {isLoading ? "Loading…" : error ? "—" : `${entries?.length ?? 0} lines`}
            {live && !isLoading && !error ? " · live" : ""}
          </span>
          <span>Newest first</span>
        </div>
      </div>

      {error && (
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          <AlertTriangle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
          <span style={{ color: "var(--crit)" }}>Couldn&apos;t reach Loki: {error.message}</span>
          <button onClick={() => mutate()} className="ml-auto text-xs font-medium underline" style={{ color: "var(--crit)" }}>
            Retry
          </button>
        </div>
      )}

      <div className="panel overflow-hidden">
        <div
          className="hidden grid-cols-[92px_74px_120px_120px_1fr_28px] gap-3 px-4 py-2 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
          style={{ background: "var(--canvas)" }}
        >
          <div>Time</div>
          <div>Level</div>
          <div>Host</div>
          <div>Source</div>
          <div>Message</div>
          <div />
        </div>

        <div className="max-h-[65vh] overflow-y-auto">
          {isLoading && <p className="p-6 text-sm text-text-faint">Loading…</p>}
          {!isLoading && !error && (entries?.length ?? 0) === 0 && (
            <p className="p-8 text-center text-sm text-text-faint">No log lines match these filters in the selected time range.</p>
          )}
          {entries?.map((entry, i) => (
            <LogRow key={`${entry.ts}-${entry.host}-${entry.source}-${i}`} entry={entry} query={q} minutes={minutes} />
          ))}
        </div>
      </div>
    </main>
  );
}
