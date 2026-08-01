"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { AnimatePresence, motion } from "framer-motion";
import {
  Siren,
  ShieldAlert,
  AlertTriangle,
  Activity,
  Server,
  Search,
  RefreshCw,
  X,
  ChevronRight,
  Sparkles,
  Cpu,
  MemoryStick,
  ExternalLink,
  ScrollText,
  ShieldCheck,
  Radar,
  History,
} from "lucide-react";
import type { AnomalyFlag, AnomalySeverity } from "@/lib/types";
import {
  ALL_SEVERITIES,
  METHOD_LABEL,
  SEVERITY_COLOR,
  SEVERITY_LABEL,
  SEVERITY_SOFT,
  SEVERITY_THRESHOLDS,
  buildInsight,
  formatDetectedAt,
  formatRelative,
  formatZScore,
  metricLabel,
  zScoreFill,
} from "@/lib/anomalies";
import { ProgressBar } from "./ui/ProgressBar";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as AnomalyFlag[];
};

type IconType = typeof Cpu;

/** Static per-metric glyph. A real component (not a dynamically resolved
 * reference) so it can be used directly as a JSX tag without React
 * remounting it on every render. */
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

const SORTS = [
  { key: "severity", label: "Severity" },
  { key: "recent", label: "Most recent" },
  { key: "zscore", label: "Z-score" },
] as const;
type SortKey = (typeof SORTS)[number]["key"];

const SEVERITY_RANK: Record<AnomalySeverity, number> = { critical: 3, high: 2, medium: 1 };

function flagKey(a: AnomalyFlag) {
  return `${a.hostname}::${a.metric_name}::${a.detected_at}`;
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

function ZScoreMeter({ z, severity }: { z: number; severity: AnomalySeverity }) {
  const fill = zScoreFill(z);
  const scale = SEVERITY_THRESHOLDS.critical + 1.5;
  const markers: { at: number; label: string }[] = [
    { at: SEVERITY_THRESHOLDS.medium / scale, label: "2σ" },
    { at: SEVERITY_THRESHOLDS.high / scale, label: "3σ" },
    { at: SEVERITY_THRESHOLDS.critical / scale, label: "4σ" },
  ];
  return (
    <div className="relative pt-1">
      <div className="relative h-2 overflow-hidden rounded-full" style={{ background: "var(--border-soft)" }}>
        <div
          className="h-full rounded-full transition-[width] duration-700 ease-out"
          style={{ width: `${fill * 100}%`, background: SEVERITY_COLOR[severity] }}
        />
        {markers.map((m) => (
          <div
            key={m.label}
            className="absolute top-0 h-full w-px"
            style={{ left: `${m.at * 100}%`, background: "var(--surface)", opacity: 0.6 }}
          />
        ))}
      </div>
      <div className="relative mt-1 h-3.5 text-[10px] text-text-faint">
        {markers.map((m) => (
          <span key={m.label} className="absolute -translate-x-1/2" style={{ left: `${m.at * 100}%` }}>
            {m.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
  active,
  onClick,
}: {
  icon: IconType;
  label: string;
  value: number;
  color: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="panel panel-interactive flex items-center gap-3 p-4 text-left transition-shadow"
      style={active ? { borderColor: color, boxShadow: `0 0 0 1px ${color}` } : undefined}
    >
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]" style={{ background: `color-mix(in srgb, ${color} 12%, transparent)` }}>
        <Icon className="h-4.5 w-4.5" style={{ color }} strokeWidth={1.75} />
      </div>
      <div className="min-w-0">
        <div className="stat-figure text-xl text-color-text">{value}</div>
        <div className="truncate text-xs text-text-faint">{label}</div>
      </div>
    </button>
  );
}

function AlertRow({ a, onOpen }: { a: AnomalyFlag; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="group grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 border-b p-4 text-left transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[110px_1.6fr_1fr_100px_120px_auto_20px]"
      style={{ borderColor: "var(--border-soft)" }}
    >
      <div className="hidden sm:block">
        <SeverityBadge severity={a.severity} />
      </div>

      <div className="col-span-2 flex items-center gap-3 sm:col-span-1">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
          <Server className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
        </div>
        <div className="min-w-0">
          <div className="truncate font-medium text-color-text">{a.hostname}</div>
          <div className="mt-0.5 flex items-center gap-1 text-xs text-text-faint sm:hidden">
            <SeverityBadge severity={a.severity} />
          </div>
        </div>
      </div>

      <div className="hidden items-center gap-1.5 text-sm text-text-dim sm:flex">
        <MetricGlyph metric={a.metric_name} className="h-3.5 w-3.5 text-text-faint" />
        {metricLabel(a.metric_name)}
      </div>

      <div className="hidden flex-col gap-1 sm:flex">
        <span className="stat-figure text-sm text-color-text">{a.current_value.toFixed(1)}%</span>
        <ProgressBar value={a.current_value} color={SEVERITY_COLOR[a.severity]} />
      </div>

      <div className="hidden sm:block">
        <span
          className="stat-figure inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold"
          style={{ color: SEVERITY_COLOR[a.severity], background: SEVERITY_SOFT[a.severity] }}
        >
          {formatZScore(a.z_score)}
        </span>
      </div>

      <div className="hidden text-right text-xs text-text-faint sm:block">{formatRelative(a.detected_at)}</div>

      <ChevronRight className="h-4 w-4 text-text-muted transition-transform group-hover:translate-x-0.5" strokeWidth={2} />
    </button>
  );
}

function Detail({ a, onClose }: { a: AnomalyFlag; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <motion.div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(10,12,16,0.45)", backdropFilter: "blur(2px)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      />
      <motion.aside
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[440px] flex-col overflow-hidden border-l"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 34 }}
      >
        <div className="glow-surface pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />
        <div className="flex items-start justify-between gap-3 border-b p-5" style={{ borderColor: "var(--border-soft)" }}>
          <div>
            <SeverityBadge severity={a.severity} />
            <h2 className="font-display mt-2 text-lg font-semibold text-color-text">{a.hostname}</h2>
            <div className="mt-0.5 flex items-center gap-1.5 text-sm text-text-faint">
              <MetricGlyph metric={a.metric_name} className="h-3.5 w-3.5" />
              {metricLabel(a.metric_name)}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          <div className="glow-insight rounded-[var(--radius-panel)] p-4">
            <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--accent)" }}>
              <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
              Generated insight
            </div>
            <p className="mt-2 text-sm leading-relaxed text-color-text">{buildInsight(a)}</p>
          </div>

          <div className="panel p-4">
            <div className="eyebrow">Current value</div>
            <div className="stat-figure mt-1 text-2xl text-color-text">{a.current_value.toFixed(1)}%</div>
            <div className="mt-3">
              <ProgressBar value={a.current_value} color={SEVERITY_COLOR[a.severity]} />
            </div>
          </div>

          <div className="panel p-4">
            <div className="flex items-center justify-between">
              <div className="eyebrow">Deviation</div>
              <span className="stat-figure text-sm" style={{ color: SEVERITY_COLOR[a.severity] }}>
                {formatZScore(a.z_score)}
              </span>
            </div>
            <ZScoreMeter z={a.z_score} severity={a.severity} />
          </div>

          <div className="panel divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
            {[
              { label: "Detection method", value: METHOD_LABEL[a.method] },
              { label: "Baseline size", value: a.baseline_n != null ? `${a.baseline_n} samples` : "warming up (EWMA)" },
              { label: "Detected at", value: formatDetectedAt(a.detected_at) },
              { label: "Relative", value: formatRelative(a.detected_at) },
            ].map((row) => (
              <div key={row.label} className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                <span className="text-text-faint">{row.label}</span>
                <span className="text-color-text">{row.value}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 border-t p-4" style={{ borderColor: "var(--border-soft)" }}>
          <Link
            href={`/nodes/${encodeURIComponent(a.hostname)}`}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} />
            View node
          </Link>
          <Link
            href={`/alerts/history?hostname=${encodeURIComponent(a.hostname)}`}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <History className="h-3.5 w-3.5" strokeWidth={2} />
            History
          </Link>
          <Link
            href="/logs"
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <ScrollText className="h-3.5 w-3.5" strokeWidth={2} />
            View logs
          </Link>
        </div>
      </motion.aside>
    </>
  );
}

export default function AlertsView() {
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState<"all" | AnomalySeverity>("all");
  const [sort, setSort] = useState<SortKey>("severity");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const { data, error, isLoading, isValidating, mutate } = useSWR<AnomalyFlag[]>("/api/anomalies", fetcher, {
    refreshInterval: 5000,
    keepPreviousData: true,
  });

  useEffect(() => {
    const onRefresh = () => mutate();
    window.addEventListener("cortex:refresh", onRefresh);
    return () => window.removeEventListener("cortex:refresh", onRefresh);
  }, [mutate]);

  const anomalies = useMemo(() => data ?? [], [data]);

  const counts = useMemo(() => {
    const c: Record<AnomalySeverity, number> = { critical: 0, high: 0, medium: 0 };
    const hosts = new Set<string>();
    for (const a of anomalies) {
      c[a.severity] = (c[a.severity] ?? 0) + 1;
      hosts.add(a.hostname);
    }
    return { ...c, hosts: hosts.size };
  }, [anomalies]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = anomalies.filter((a) => {
      if (severity !== "all" && a.severity !== severity) return false;
      if (!q) return true;
      return a.hostname.toLowerCase().includes(q) || metricLabel(a.metric_name).toLowerCase().includes(q);
    });
    list = [...list].sort((a, b) => {
      if (sort === "severity") return SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || Math.abs(b.z_score) - Math.abs(a.z_score);
      if (sort === "zscore") return Math.abs(b.z_score) - Math.abs(a.z_score);
      return new Date(b.detected_at).getTime() - new Date(a.detected_at).getTime();
    });
    return list;
  }, [anomalies, search, severity, sort]);

  const selected = anomalies.find((a) => flagKey(a) === selectedKey) ?? null;
  const hasCritical = counts.critical > 0;

  return (
    <main className="grid gap-4">
      <div className="glow-surface panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <Siren className="h-4.5 w-4.5 text-text-dim" strokeWidth={1.75} />
          </div>
          <div>
            <div className="eyebrow">Monitoring</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Alerts</h1>
            <div className="mt-0.5 text-sm text-text-faint">Live anomaly detection across the fleet</div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span
            className="glow-pulse inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium"
            style={
              {
                border: "1px solid var(--border)",
                color: hasCritical ? "var(--crit)" : "var(--ok)",
                background: hasCritical ? "var(--crit-soft)" : "var(--ok-soft)",
                "--pulse-color": hasCritical ? "var(--crit)" : "var(--ok)",
              } as React.CSSProperties
            }
          >
            <span className="status-dot" style={{ background: hasCritical ? "var(--crit)" : "var(--ok)" }} />
            {hasCritical ? "Attention needed" : "All clear"}
          </span>
          <button
            onClick={() => mutate()}
            aria-label="Refresh alerts"
            className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
          </button>
          <Link
            href="/alerts/history"
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium text-text-dim transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)" }}
          >
            <History className="h-3.5 w-3.5" strokeWidth={2} />
            History
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard icon={ShieldAlert} label="Critical" value={counts.critical} color="var(--crit)" active={severity === "critical"} onClick={() => setSeverity(severity === "critical" ? "all" : "critical")} />
        <StatCard icon={AlertTriangle} label="High" value={counts.high} color="var(--warn)" active={severity === "high"} onClick={() => setSeverity(severity === "high" ? "all" : "high")} />
        <StatCard icon={Activity} label="Medium" value={counts.medium} color="var(--medium)" active={severity === "medium"} onClick={() => setSeverity(severity === "medium" ? "all" : "medium")} />
        <StatCard icon={Radar} label="Affected hosts" value={counts.hosts} color="var(--accent)" active={false} onClick={() => setSeverity("all")} />
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
            <button
              onClick={() => setSeverity("all")}
              className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
              style={{ background: severity === "all" ? "var(--accent)" : "transparent", color: severity === "all" ? "#fff" : "var(--text-dim)" }}
            >
              All
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

          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            className="ml-auto rounded-[var(--radius-control)] px-3 py-2 text-sm text-color-text outline-none transition-colors"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                Sort: {s.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center justify-between px-0.5 text-xs text-text-faint">
          <span>
            {isLoading ? "Loading…" : error ? "—" : `${filtered.length} alert${filtered.length === 1 ? "" : "s"}`}
            {!isLoading && !error ? " · live" : ""}
          </span>
          <span>Highest severity first</span>
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
          className="hidden grid-cols-[110px_1.6fr_1fr_100px_120px_auto_20px] gap-3 px-4 py-2 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
          style={{ background: "var(--canvas)" }}
        >
          <div>Severity</div>
          <div>Host</div>
          <div>Metric</div>
          <div>Value</div>
          <div>Z-score</div>
          <div>Detected</div>
          <div />
        </div>

        <div className="max-h-[60vh] overflow-y-auto">
          {isLoading && <p className="p-6 text-sm text-text-faint">Loading…</p>}
          {!isLoading && !error && filtered.length === 0 && (
            <div className="flex flex-col items-center gap-2 p-10 text-center">
              <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
              <p className="text-sm text-text-faint">
                {anomalies.length === 0 ? "No active anomalies — every host is within its normal baseline." : "No alerts match these filters."}
              </p>
            </div>
          )}
          {filtered.map((a) => (
            <AlertRow key={flagKey(a)} a={a} onOpen={() => setSelectedKey(flagKey(a))} />
          ))}
        </div>
      </div>

      <AnimatePresence>{selected && <Detail a={selected} onClose={() => setSelectedKey(null)} />}</AnimatePresence>
    </main>
  );
}
