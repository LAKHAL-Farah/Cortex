"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { AnimatePresence, motion } from "framer-motion";
import {
  History,
  ArrowLeft,
  Search,
  RefreshCw,
  AlertTriangle,
  Server,
  Boxes,
  ShieldCheck,
  CheckCircle2,
  Cpu,
  MemoryStick,
  Activity,
  ChevronRight,
  Sparkles,
  ExternalLink,
  ScrollText,
  X,
} from "lucide-react";
import type { AnomalyEvent, AnomalySeverity } from "@/lib/types";
import {
  ALL_SEVERITIES,
  SEVERITY_COLOR,
  SEVERITY_LABEL,
  SEVERITY_SOFT,
  SEVERITY_THRESHOLDS,
  METHOD_LABEL,
  formatDetectedAt,
  formatDuration,
  formatRelative,
  formatZScore,
  isServiceStateMetric,
  metricLabel,
  parseServiceId,
  serviceDisplayName,
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
  return data as AnomalyEvent[];
};

type StatusFilter = "all" | "active" | "resolved";

function MetricGlyph({ metric, className }: { metric: string; className?: string }) {
  switch (metric) {
    case "cpu_usage":
      return <Cpu className={className} strokeWidth={1.75} />;
    case "ram_usage":
      return <MemoryStick className={className} strokeWidth={1.75} />;
    // Same reasoning as AlertsView.tsx's MetricGlyph -- a service_state
    // event is a service, not a host metric, so it gets the topology
    // graph's :Service glyph (see lib/topology.ts's LABEL_ICON.Service).
    case "service_state":
      return <Boxes className={className} strokeWidth={1.75} />;
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

function EventRow({ e, onOpen }: { e: AnomalyEvent; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="group grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 border-b p-4 text-left transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[110px_1.4fr_1fr_90px_140px_120px_20px]"
      style={{ borderColor: "var(--border-soft)" }}
    >
      <div className="hidden sm:block">
        <SeverityBadge severity={e.severity} />
      </div>

      <div className="col-span-2 flex items-center gap-3 sm:col-span-1">
        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
          {isServiceStateMetric(e.metric_name) ? (
            <Boxes className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
          ) : (
            <Server className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
          )}
        </div>
        <div className="min-w-0">
          {isServiceStateMetric(e.metric_name) ? (
            <>
              <div className="truncate font-medium text-color-text">{serviceDisplayName(e.hostname)}</div>
              <div className="truncate text-xs text-text-faint">on {parseServiceId(e.hostname)?.host ?? e.hostname}</div>
            </>
          ) : (
            <div className="truncate font-medium text-color-text">{e.hostname}</div>
          )}
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
        {isServiceStateMetric(e.metric_name) ? (
          <span className="text-xs text-text-faint">—</span>
        ) : (
          <span
            className="stat-figure inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold"
            style={{ color: SEVERITY_COLOR[e.severity], background: SEVERITY_SOFT[e.severity] }}
          >
            {formatZScore(e.z_score)}
          </span>
        )}
      </div>

      <div className="hidden flex-col text-xs text-text-faint sm:flex">
        <span title={formatDetectedAt(e.started_at)}>Started {formatRelative(e.started_at)}</span>
        <span className="stat-figure mt-0.5 text-color-text">Lasted {formatDuration(e.started_at, e.resolved_at)}</span>
      </div>

      <div className="flex items-center justify-end sm:justify-start">
        <StatusBadge isActive={e.is_active} />
      </div>

      <ChevronRight className="hidden h-4 w-4 text-text-muted transition-transform group-hover:translate-x-0.5 sm:block" strokeWidth={2} />
    </button>
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

/** One-line, deterministic summary of a past episode, mirroring
 * lib/anomalies.ts::buildInsight but phrased around a peak reading over a
 * (possibly finished) window instead of a single live sample. */
function buildEventInsight(e: AnomalyEvent): string {
  const status = e.is_active
    ? "It's still active."
    : `It lasted ${formatDuration(e.started_at, e.resolved_at)} before resolving.`;

  if (isServiceStateMetric(e.metric_name)) {
    // Same distinction as lib/anomalies.ts::buildInsight -- a
    // service_state episode has no percentage/sigma peak, it's a
    // stretch of time the service wasn't in its expected running state.
    const parsed = parseServiceId(e.hostname);
    const subject = parsed ? `${serviceDisplayName(e.hostname)} on ${parsed.host}` : e.hostname;
    return `${subject} wasn't reporting its expected running state, peaking at ${SEVERITY_LABEL[e.severity].toLowerCase()} severity — a live service state check, not a statistical metric comparison. ${status}`;
  }

  const metric = metricLabel(e.metric_name);
  const dir = e.z_score >= 0 ? "above" : "below";
  const magnitude = Math.abs(e.z_score).toFixed(1);
  const confidence =
    e.method === "ewma_fallback"
      ? "a short-term EWMA estimate, since this host/hour slot didn't have enough history yet"
      : `a baseline of ${e.baseline_n ?? "—"} samples for this weekday and hour`;

  return `${e.hostname}'s ${metric.toLowerCase()} peaked at ${e.current_value.toFixed(1)}%, ${magnitude}σ ${dir} what's typical here — based on ${confidence}. ${status}`;
}

function Detail({ e, onClose }: { e: AnomalyEvent; onClose: () => void }) {
  const isService = isServiceStateMetric(e.metric_name);
  const service = isService ? parseServiceId(e.hostname) : null;
  const nodeHostname = service?.host ?? e.hostname;

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
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <SeverityBadge severity={e.severity} />
              <StatusBadge isActive={e.is_active} />
            </div>
            <h2 className="font-display mt-2 truncate text-lg font-semibold text-color-text">
              {isService ? serviceDisplayName(e.hostname) : e.hostname}
            </h2>
            <div className="mt-0.5 flex items-center gap-1.5 text-sm text-text-faint">
              <MetricGlyph metric={e.metric_name} className="h-3.5 w-3.5" />
              {isService ? `Service · runs on ${nodeHostname}` : metricLabel(e.metric_name)}
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
            <p className="mt-2 text-sm leading-relaxed text-color-text">{buildEventInsight(e)}</p>
          </div>

          {isService ? (
            // Same reasoning as AlertsView.tsx's Detail panel: a
            // service_state episode never had a percentage/sigma peak to
            // show, so show what it actually was instead of a fabricated
            // "Peak value"/"Peak deviation" pair.
            <div className="panel overflow-hidden">
              <div className="flex items-center gap-1.5 px-4 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted" style={{ background: "var(--canvas)" }}>
                <Boxes className="h-3.5 w-3.5" strokeWidth={2} />
                Service
              </div>
              <div className="divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                  <span className="text-text-faint">Service</span>
                  <span className="text-color-text">{serviceDisplayName(e.hostname)}</span>
                </div>
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                  <span className="text-text-faint">Running on</span>
                  <Link href={`/topology?highlight=${encodeURIComponent(e.hostname)},${encodeURIComponent(nodeHostname)}`} className="truncate underline" style={{ color: "var(--accent)" }}>
                    {nodeHostname}
                  </Link>
                </div>
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                  <span className="text-text-faint">Peak severity</span>
                  <span className="font-medium" style={{ color: SEVERITY_COLOR[e.severity] }}>
                    {SEVERITY_LABEL[e.severity]}
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="panel p-4">
                <div className="eyebrow">Peak value</div>
                <div className="stat-figure mt-1 text-2xl text-color-text">{e.current_value.toFixed(1)}%</div>
                <div className="mt-3">
                  <ProgressBar value={e.current_value} color={SEVERITY_COLOR[e.severity]} />
                </div>
              </div>

              <div className="panel p-4">
                <div className="flex items-center justify-between">
                  <div className="eyebrow">Peak deviation</div>
                  <span className="stat-figure text-sm" style={{ color: SEVERITY_COLOR[e.severity] }}>
                    {formatZScore(e.z_score)}
                  </span>
                </div>
                <ZScoreMeter z={e.z_score} severity={e.severity} />
              </div>
            </>
          )}

          <div className="panel divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
            {[
              isService
                ? { label: "Detection method", value: "Live service state check" }
                : { label: "Detection method", value: METHOD_LABEL[e.method] },
              ...(isService ? [] : [{ label: "Baseline size", value: e.baseline_n != null ? `${e.baseline_n} samples` : "warming up (EWMA)" }]),
              { label: "Started at", value: formatDetectedAt(e.started_at) },
              { label: "Started", value: formatRelative(e.started_at) },
              { label: "Resolved at", value: e.resolved_at ? formatDetectedAt(e.resolved_at) : "Still active" },
              { label: "Duration", value: formatDuration(e.started_at, e.resolved_at) },
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
            href={`/nodes/${encodeURIComponent(nodeHostname)}`}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} />
            {isService ? "View host" : "View node"}
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

export default function AnomalyHistoryView() {
  const searchParams = useSearchParams();
  const hostnameParam = searchParams.get("hostname") ?? "";

  const [search, setSearch] = useState(hostnameParam);
  const [severity, setSeverity] = useState<"all" | AnomalySeverity>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

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
  const selected = events.find((e) => e.id === selectedId) ?? null;

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
            <EventRow key={e.id} e={e} onOpen={() => setSelectedId(e.id)} />
          ))}
        </div>
      </div>

      <AnimatePresence>{selected && <Detail e={selected} onClose={() => setSelectedId(null)} />}</AnimatePresence>
    </main>
  );
}
