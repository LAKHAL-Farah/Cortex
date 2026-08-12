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
  ChevronDown,
  Sparkles,
  Cpu,
  MemoryStick,
  ExternalLink,
  ScrollText,
  ShieldCheck,
  Radar,
  History,
  Link2,
  Waypoints,
} from "lucide-react";
import type { AnomalyFlag, AnomalyIncident, AnomalySeverity, RcaSuggestion } from "@/lib/types";
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
  isServiceStateMetric,
  metricLabel,
  zScoreFill,
} from "@/lib/anomalies";
import { relationshipLabel } from "@/lib/rca";
import { ProgressBar } from "./ui/ProgressBar";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as AnomalyIncident[];
};

/** Same fetcher shape, just typed for /api/anomalies/rca's array-of-
 * suggestions response instead of an array of incidents. */
const rcaFetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as RcaSuggestion[];
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

/** An incident with more than one member (action plan doc, section 3.3):
 * a narrative + severity + member count that expands to the same
 * AlertRow used for a standalone alert, plus a "View on graph" deep link
 * into /topology when the API resolved a graph_path for it. Incidents
 * with only one surviving member render as a plain AlertRow instead (see
 * the grouping logic in AlertsView below) -- this component is only ever
 * used for genuinely correlated groups.
 */
function IncidentCard({
  incident,
  members,
  onOpenMember,
}: {
  incident: AnomalyIncident;
  members: AnomalyFlag[];
  onOpenMember: (a: AnomalyFlag) => void;
}) {
  // Starts expanded (unlike a typical accordion) -- this is a new kind of
  // row in this list, so it should be obvious at a glance rather than
  // requiring a click to discover.
  const [expanded, setExpanded] = useState(true);
  const graphHref = incident.graph_path
    ? `/topology?highlight=${incident.graph_path.vertex_ids.map(encodeURIComponent).join(",")}`
    : null;

  return (
    <div
      className="border-b last:border-b-0"
      style={{ borderColor: "var(--border-soft)", borderLeft: `3px solid ${SEVERITY_COLOR[incident.severity]}`, background: SEVERITY_SOFT[incident.severity] }}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        className="grid w-full grid-cols-[auto_1fr_auto] items-start gap-3 p-4 text-left transition-colors hover:bg-[var(--canvas)]"
      >
        <div className="mt-0.5">
          <SeverityBadge severity={incident.severity} />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.06em]" style={{ color: SEVERITY_COLOR[incident.severity] }}>
            <Link2 className="h-3.5 w-3.5 flex-shrink-0" strokeWidth={2.5} />
            Correlated incident · {members.length} related alerts
          </div>
          <p className="mt-1 text-sm font-medium leading-relaxed text-color-text">{incident.narrative}</p>
        </div>
        <ChevronDown
          className={`h-4 w-4 flex-shrink-0 text-text-muted transition-transform ${expanded ? "rotate-180" : ""}`}
          strokeWidth={2}
        />
      </button>

      {expanded && (
        <div className="space-y-2 pb-3">
          {graphHref && (
            <Link
              href={graphHref}
              className="mx-4 inline-flex items-center gap-1.5 text-xs font-semibold underline"
              style={{ color: "var(--accent)" }}
            >
              <Waypoints className="h-3.5 w-3.5" strokeWidth={2} />
              View on graph
            </Link>
          )}
          <div className="mx-4 overflow-hidden rounded-[var(--radius-control)]" style={{ border: "1px solid var(--border-soft)", background: "var(--surface)" }}>
            {members.map((m) => (
              <AlertRow key={flagKey(m)} a={m} onOpen={() => onOpenMember(m)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** One "X caused Y" suggestion (basic causal RCA -- see
 * services/api/app/services/rca_suggester.py). Renders the API's own
 * `text` field directly rather than recomposing it client-side, same
 * reasoning IncidentCard's narrative and buildInsight() already follow:
 * one source of truth for the sentence. The relationship badge is a
 * secondary, glanceable cue on top of that sentence, not a replacement
 * for it. */
function RcaSuggestionRow({ suggestion }: { suggestion: RcaSuggestion }) {
  return (
    <div
      className="flex items-start gap-3 border-b p-4 last:border-b-0"
      style={{ borderColor: "var(--border-soft)" }}
    >
      <div
        className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
        style={{ background: SEVERITY_SOFT[suggestion.effect.severity] }}
      >
        <Waypoints className="h-4 w-4" style={{ color: SEVERITY_COLOR[suggestion.effect.severity] }} strokeWidth={1.75} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-text-faint">
          <SeverityBadge severity={suggestion.cause.severity} />
          <span className="truncate font-semibold text-color-text">{suggestion.cause.id}</span>
          <span>{relationshipLabel(suggestion.relationship)}</span>
          <span className="truncate font-semibold text-color-text">{suggestion.effect.id}</span>
        </div>
        <p className="mt-1.5 text-sm leading-relaxed text-color-text">{suggestion.text}</p>
      </div>
    </div>
  );
}

/** "RCA suggestions" section, above the alerts list -- see the action
 * plan doc's step 4. Each row is a single-hop, graph-adjacent cause/effect
 * pair; this is additive to the incident/alert list below, not a
 * replacement for it (an incident can have zero RCA suggestions if none
 * of its members are graph-adjacent, and vice versa). */
function RcaSuggestionsPanel({ suggestions }: { suggestions: RcaSuggestion[] }) {
  if (suggestions.length === 0) return null;
  return (
    <div className="panel overflow-hidden">
      <div
        className="flex items-center gap-1.5 px-4 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted"
        style={{ background: "var(--canvas)" }}
      >
        <Link2 className="h-3.5 w-3.5" strokeWidth={2} />
        RCA suggestions · likely root causes
      </div>
      <div>
        {suggestions.map((s) => (
          <RcaSuggestionRow key={`${s.cause.id}::${s.effect.id}::${s.relationship}`} suggestion={s} />
        ))}
      </div>
    </div>
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

          {/* current_value/z_score are meaningless for a service_state flag --
             it's a live up/down/unreachable check, not a percentage metric
             with a statistical deviation, so the value + sigma panels below
             don't apply and are skipped for it. */}
          {!isServiceStateMetric(a.metric_name) && (
            <>
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
            </>
          )}

          <div className="panel divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
            {[
              isServiceStateMetric(a.metric_name)
                ? { label: "Detection method", value: "Live service state check" }
                : { label: "Detection method", value: METHOD_LABEL[a.method] },
              ...(isServiceStateMetric(a.metric_name)
                ? []
                : [{ label: "Baseline size", value: a.baseline_n != null ? `${a.baseline_n} samples` : "warming up (EWMA)" }]),
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

  const { data, error, isLoading, isValidating, mutate } = useSWR<AnomalyIncident[]>("/api/anomalies/incidents", fetcher, {
    refreshInterval: 5000,
    keepPreviousData: true,
  });

  // Separate SWR key/poll from incidents: the RCA endpoint can 503 when
  // the graph is briefly unreachable (see routers/anomalies.py's /rca)
  // even while /incidents keeps serving ungrouped alerts fine, so a
  // failure here shouldn't be surfaced as a hard error for the whole
  // page -- just an absent (not shown) suggestions panel.
  const { data: rcaData, mutate: mutateRca } = useSWR<RcaSuggestion[]>("/api/anomalies/rca", rcaFetcher, {
    refreshInterval: 5000,
    keepPreviousData: true,
    shouldRetryOnError: true,
  });

  useEffect(() => {
    const onRefresh = () => {
      mutate();
      mutateRca();
    };
    window.addEventListener("cortex:refresh", onRefresh);
    return () => window.removeEventListener("cortex:refresh", onRefresh);
  }, [mutate, mutateRca]);

  const incidents = useMemo(() => data ?? [], [data]);

  // Every open alert, alongside the incident it belongs to -- flattened
  // back out so counts/search/sort keep working over individual alerts
  // exactly as they did before incidents existed, per the action plan
  // doc's "this is additive, not a rewrite of the single-alert case".
  const flatMembers = useMemo(
    () => incidents.flatMap((incident) => incident.members.map((member) => ({ member, incident }))),
    [incidents]
  );

  const counts = useMemo(() => {
    const c: Record<AnomalySeverity, number> = { critical: 0, high: 0, medium: 0 };
    const hosts = new Set<string>();
    for (const { member } of flatMembers) {
      c[member.severity] = (c[member.severity] ?? 0) + 1;
      hosts.add(member.hostname);
    }
    return { ...c, hosts: hosts.size };
  }, [flatMembers]);

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matches = (m: AnomalyFlag) => {
      if (severity !== "all" && m.severity !== severity) return false;
      if (!q) return true;
      return m.hostname.toLowerCase().includes(q) || metricLabel(m.metric_name).toLowerCase().includes(q);
    };

    const byIncident = new Map<string, { incident: AnomalyIncident; members: AnomalyFlag[] }>();
    for (const { member, incident } of flatMembers) {
      if (!matches(member)) continue;
      const existing = byIncident.get(incident.incident_id);
      if (existing) existing.members.push(member);
      else byIncident.set(incident.incident_id, { incident, members: [member] });
    }

    const bestSeverityRank = (ms: AnomalyFlag[]) => Math.max(...ms.map((m) => SEVERITY_RANK[m.severity]));
    const bestZScore = (ms: AnomalyFlag[]) => Math.max(...ms.map((m) => Math.abs(m.z_score)));
    const mostRecent = (ms: AnomalyFlag[]) => Math.max(...ms.map((m) => new Date(m.detected_at).getTime()));

    const list = [...byIncident.values()];
    list.sort((a, b) => {
      if (sort === "severity") return bestSeverityRank(b.members) - bestSeverityRank(a.members) || bestZScore(b.members) - bestZScore(a.members);
      if (sort === "zscore") return bestZScore(b.members) - bestZScore(a.members);
      return mostRecent(b.members) - mostRecent(a.members);
    });
    return list;
  }, [flatMembers, search, severity, sort]);

  const shownCount = useMemo(() => groups.reduce((n, g) => n + g.members.length, 0), [groups]);
  const selected = flatMembers.find(({ member }) => flagKey(member) === selectedKey)?.member ?? null;
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
            {isLoading ? "Loading…" : error ? "—" : `${shownCount} alert${shownCount === 1 ? "" : "s"}`}
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

      {/* Basic causal RCA suggestions (see rca_suggester.py) -- sits above
         the alerts list itself, additive to it. Silently absent (not an
         error banner) when the endpoint 503s or simply has nothing to
         say yet -- /incidents above is still the source of truth for
         whether alerting itself is healthy. */}
      <RcaSuggestionsPanel suggestions={rcaData ?? []} />

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
          {!isLoading && !error && groups.length === 0 && (
            <div className="flex flex-col items-center gap-2 p-10 text-center">
              <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
              <p className="text-sm text-text-faint">
                {flatMembers.length === 0 ? "No active anomalies — every host is within its normal baseline." : "No alerts match these filters."}
              </p>
            </div>
          )}
          {groups.map((g) =>
            g.members.length > 1 ? (
              <IncidentCard
                key={g.incident.incident_id}
                incident={g.incident}
                members={g.members}
                onOpenMember={(m) => setSelectedKey(flagKey(m))}
              />
            ) : (
              <AlertRow key={flagKey(g.members[0])} a={g.members[0]} onOpen={() => setSelectedKey(flagKey(g.members[0]))} />
            )
          )}
        </div>
      </div>

      <AnimatePresence>{selected && <Detail a={selected} onClose={() => setSelectedKey(null)} />}</AnimatePresence>
    </main>
  );
}
