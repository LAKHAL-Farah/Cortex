"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  History,
  ArrowLeft,
  Search,
  RefreshCw,
  AlertTriangle,
  Server,
  ShieldCheck,
  CheckCircle2,
  Cpu,
  MemoryStick,
  Activity,
} from "lucide-react";
import type { AnomalyEvent, AnomalySeverity } from "@/lib/types";
import {
  ALL_SEVERITIES,
  SEVERITY_COLOR,
  SEVERITY_LABEL,
  SEVERITY_SOFT,
  formatDetectedAt,
  formatDuration,
  formatRelative,
  formatZScore,
  metricLabel,
} from "@/lib/anomalies";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as AnomalyEvent[];
};

type StatusFilter = "all" | "active" | "resolved";

function MetricGlyph({ metric, className }: { metric: string; className?: string }) {
  switch (metric) {
    case "cpu_usage":
      return <Cpu className={className} strokeWidth={1.75} />;
    case "ram_usage":
      return <MemoryStick className={className} strokeWidth={1.75} />;
    default:
      return <Activity className={className} strokeWidth={1.75} />;
  }
}

function SeverityBadge({ severity }: { severity: AnomalySeverity }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ color: SEVERITY_COLOR[severity], background: SEVERITY_SOFT[severity] }}
    >
      <span className="status-dot" style={{ background: SEVERITY_COLOR[severity] }} />
      {SEVERITY_LABEL[severity]}
    </span>
  );
}

function StatusBadge({ isActive }: { isActive: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{
        color: isActive ? "var(--crit)" : "var(--ok)",
        background: isActive ? "var(--crit-soft)" : "var(--ok-soft)",
      }}
    >
      {isActive ? <span className="status-dot" style={{ background: "var(--crit)" }} /> : <CheckCircle2 className="h-3 w-3" strokeWidth={2} />}
      {isActive ? "Still active" : "Resolved"}
    </span>
  );
}

function EventRow({ e }: { e: AnomalyEvent }) {
  return (
    <div
      className="grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 border-b p-4 text-left last:border-b-0 sm:grid-cols-[110px_1.4fr_1fr_90px_140px_120px]"
      style={{ borderColor: "var(--border-soft)" }}
    >
      <div className="hidden sm:block">
        <SeverityBadge severity={e.severity} />
      </div>

      <div className="col-span-2 flex items-center gap-3 sm:col-span-1">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
          <Server className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
        </div>
        <div className="min-w-0">
          <div className="truncate font-medium text-color-text">{e.hostname}</div>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-text-faint sm:hidden">
            <SeverityBadge severity={e.severity} />
          </div>
        </div>
      </div>

      <div className="hidden items-center gap-1.5 text-sm text-text-dim sm:flex">
        <MetricGlyph metric={e.metric_name} className="h-3.5 w-3.5 text-text-faint" />
        {metricLabel(e.metric_name)}
      </div>

      <div className="hidden sm:block">
        <span
          className="stat-figure inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold"
          style={{ color: SEVERITY_COLOR[e.severity], background: SEVERITY_SOFT[e.severity] }}
        >
          {formatZScore(e.z_score)}
        </span>
      </div>

      <div className="hidden flex-col text-xs text-text-faint sm:flex">
        <span title={formatDetectedAt(e.started_at)}>Started {formatRelative(e.started_at)}</span>
        <span className="stat-figure mt-0.5 text-color-text">Lasted {formatDuration(e.started_at, e.resolved_at)}</span>
      </div>

      <div className="flex items-center justify-end">
        <StatusBadge isActive={e.is_active} />
      </div>
    </div>
  );
}

export default function AnomalyHistoryView() {
  const searchParams = useSearchParams();
  const hostnameParam = searchParams.get("hostname") ?? "";

  const [search, setSearch] = useState(hostnameParam);
  const [severity, setSeverity] = useState<"all" | AnomalySeverity>("all");
  const [status, setStatus] = useState<StatusFilter>("all");

  const { data, error, isLoading, isValidating, mutate } = useSWR<AnomalyEvent[]>("/api/anomalies/history", fetcher, {
    refreshInterval: 15000,
    keepPreviousData: true,
  });

  const events = useMemo(() => data ?? [], [data]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return events.filter((e) => {
      if (severity !== "all" && e.severity !== severity) return false;
      if (status === "active" && !e.is_active) return false;
      if (status === "resolved" && e.is_active) return false;
      if (!q) return true;
      return e.hostname.toLowerCase().includes(q) || metricLabel(e.metric_name).toLowerCase().includes(q);
    });
  }, [events, search, severity, status]);

  const activeCount = events.filter((e) => e.is_active).length;

  return (
    <main className="grid gap-4">
      <Link href="/alerts" className="inline-flex w-fit items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
        <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
        Alerts
      </Link>

      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <History className="h-4.5 w-4.5 text-text-dim" strokeWidth={1.75} />
          </div>
          <div>
            <div className="eyebrow">Monitoring</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Anomaly history</h1>
            <div className="mt-0.5 text-sm text-text-faint">Every anomaly the fleet has raised, resolved or not</div>
          </div>
        </div>

        <button
          onClick={() => mutate()}
          aria-label="Refresh history"
          className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
          style={{ border: "1px solid var(--border)" }}
        >
          <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
        </button>
      </div>

      <div className="panel grid gap-3 p-4">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" strokeWidth={2} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by host or metric…"
            className="w-full rounded-[var(--radius-control)] py-2.5 pl-9 pr-3.5 text-sm text-color-text outline-none transition-colors"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <div className="inline-flex rounded-[var(--radius-control)] p-0.5" style={{ border: "1px solid var(--border)" }}>
            {(["all", "active", "resolved"] as StatusFilter[]).map((s) => (
              <button
                key={s}
                onClick={() => setStatus(s)}
                className="rounded-[5px] px-2.5 py-1 text-xs font-medium capitalize transition-colors"
                style={{ background: status === s ? "var(--accent)" : "transparent", color: status === s ? "#fff" : "var(--text-dim)" }}
              >
                {s}
              </button>
            ))}
          </div>

          <div className="inline-flex rounded-[var(--radius-control)] p-0.5" style={{ border: "1px solid var(--border)" }}>
            <button
              onClick={() => setSeverity("all")}
              className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
              style={{ background: severity === "all" ? "var(--accent)" : "transparent", color: severity === "all" ? "#fff" : "var(--text-dim)" }}
            >
              All severities
            </button>
            {ALL_SEVERITIES.map((s) => (
              <button
                key={s}
                onClick={() => setSeverity(s)}
                className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
                style={{ background: severity === s ? SEVERITY_COLOR[s] : "transparent", color: severity === s ? "#fff" : "var(--text-dim)" }}
              >
                {SEVERITY_LABEL[s]}
              </button>
            ))}
          </div>

          <span className="ml-auto text-xs text-text-faint">{activeCount} still active</span>
        </div>

        <div className="flex items-center justify-between px-0.5 text-xs text-text-faint">
          <span>
            {isLoading ? "Loading…" : error ? "—" : `${filtered.length} event${filtered.length === 1 ? "" : "s"}`}
          </span>
          <span>Most recent first</span>
        </div>
      </div>

      {error && (
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          <AlertTriangle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
          <span style={{ color: "var(--crit)" }}>Couldn&apos;t reach the anomaly service: {error.message}</span>
          <button onClick={() => mutate()} className="ml-auto text-xs font-medium underline" style={{ color: "var(--crit)" }}>
            Retry
          </button>
        </div>
      )}

      <div className="panel overflow-hidden">
        <div
          className="hidden grid-cols-[110px_1.4fr_1fr_90px_140px_120px] gap-3 px-4 py-2 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
          style={{ background: "var(--canvas)" }}
        >
          <div>Severity</div>
          <div>Host</div>
          <div>Metric</div>
          <div>Peak Z</div>
          <div>Timing</div>
          <div className="text-right">Status</div>
        </div>

        <div className="max-h-[70vh] overflow-y-auto">
          {isLoading && <p className="p-6 text-sm text-text-faint">Loading…</p>}
          {!isLoading && !error && filtered.length === 0 && (
            <div className="flex flex-col items-center gap-2 p-10 text-center">
              <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
              <p className="text-sm text-text-faint">
                {events.length === 0 ? "No anomalies recorded yet — history fills in as the fleet gets flagged." : "No events match these filters."}
              </p>
            </div>
          )}
          {filtered.map((e) => (
            <EventRow key={e.id} e={e} />
          ))}
        </div>
      </div>
    </main>
  );
}
