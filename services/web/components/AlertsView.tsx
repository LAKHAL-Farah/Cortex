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
  Boxes,
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
  ArrowRight,
  Crosshair,
  Inbox,
  CheckCircle2,
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
  formatMetricValue,
  formatRelative,
  formatZScore,
  isServiceStateMetric,
  metricLabel,
  parseServiceId,
  serviceDisplayName,
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
    // Same glyph the topology graph itself uses for a :Service vertex
    // (see lib/topology.ts's LABEL_ICON.Service) -- this is a service,
    // not a scored host metric, so it gets the service icon rather than
    // a generic pulse.
    case "service_state":
      return <Boxes className={className} strokeWidth={1.75} />;
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
          {isServiceStateMetric(a.metric_name) ? (
            <Boxes className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
          ) : (
            <Server className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
          )}
        </div>
        <div className="min-w-0">
          {isServiceStateMetric(a.metric_name) ? (
            <>
              <div className="truncate font-medium text-color-text">{serviceDisplayName(a.hostname)}</div>
              <div className="truncate text-xs text-text-faint">on {parseServiceId(a.hostname)?.host ?? a.hostname}</div>
            </>
          ) : (
            <div className="truncate font-medium text-color-text">{a.hostname}</div>
          )}
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
        {isServiceStateMetric(a.metric_name) ? (
          <span className="text-xs text-text-faint">live check</span>
        ) : (
          <>
            <span className="stat-figure text-sm text-color-text">{formatMetricValue(a.metric_name, a.current_value)}</span>
            <ProgressBar value={a.current_value} color={SEVERITY_COLOR[a.severity]} />
          </>
        )}
      </div>

      <div className="hidden sm:block">
        {isServiceStateMetric(a.metric_name) ? (
          <span className="text-xs text-text-faint">—</span>
        ) : (
          <span
            className="stat-figure inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold"
            style={{ color: SEVERITY_COLOR[a.severity], background: SEVERITY_SOFT[a.severity] }}
          >
            {formatZScore(a.z_score)}
          </span>
        )}
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
  // Root-cause-only highlight, distinct from graphHref above (which
  // highlights the whole connected path) -- a single-vertex deep link so
  // "show me just the thing that started this" is one click, not "find
  // the right node in a highlighted subgraph".
  const rootCauseHref = incident.root_cause_guess
    ? `/topology?highlight=${encodeURIComponent(incident.root_cause_guess.vertex_id)}`
    : null;
  const rootCauseName = incident.root_cause_guess
    ? incident.root_cause_guess.label === "Service"
      ? serviceDisplayName(incident.root_cause_guess.vertex_id)
      : incident.root_cause_guess.vertex_id
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
          {rootCauseName && (
            <div
              className="mt-2 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold"
              style={{ color: SEVERITY_COLOR[incident.severity], background: "var(--surface)", border: `1px solid ${SEVERITY_COLOR[incident.severity]}` }}
            >
              <Crosshair className="h-3 w-3" strokeWidth={2.5} />
              Likely root cause: {rootCauseName}
            </div>
          )}
        </div>
        <ChevronDown
          className={`h-4 w-4 flex-shrink-0 text-text-muted transition-transform ${expanded ? "rotate-180" : ""}`}
          strokeWidth={2}
        />
      </button>

      {expanded && (
        <div className="space-y-2 pb-3">
          <div className="mx-4 flex flex-wrap items-center gap-x-4 gap-y-1">
            {rootCauseHref && (
              <Link
                href={rootCauseHref}
                className="inline-flex items-center gap-1.5 text-xs font-semibold underline"
                style={{ color: SEVERITY_COLOR[incident.severity] }}
              >
                <Crosshair className="h-3.5 w-3.5" strokeWidth={2} />
                Locate root cause on graph
              </Link>
            )}
            {graphHref && (
              <Link
                href={graphHref}
                className="inline-flex items-center gap-1.5 text-xs font-semibold underline"
                style={{ color: "var(--accent)" }}
              >
                <Waypoints className="h-3.5 w-3.5" strokeWidth={2} />
                View full blast radius on graph
              </Link>
            )}
          </div>
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

/** Small icon per RCA endpoint label -- a light echo of lib/topology.ts's
 * vertexIcon (Server for :Node, Boxes for :Service) without importing the
 * full TopologyVertex-shaped helper, since RcaEndpoint only ever carries
 * id/label/metric_name/severity, not full graph properties. */
function EndpointGlyph({ label, className }: { label: RcaSuggestion["cause"]["label"]; className?: string }) {
  if (label === "Service") return <Boxes className={className} strokeWidth={1.75} />;
  if (label === "Node" || label === null) return <Server className={className} strokeWidth={1.75} />;
  return <Waypoints className={className} strokeWidth={1.75} />;
}

/** One endpoint of a cause->effect pair, as its own visual block rather
 * than an inline clause -- this is what makes "which one is the cause"
 * legible at a glance instead of requiring the reader to parse a
 * sentence. `role` only changes the eyebrow label/color; the shape is
 * identical for both sides so cause and effect read as visually
 * comparable, not as a "before/after" hierarchy. */
function RcaEndpointBlock({ endpoint, role }: { endpoint: RcaSuggestion["cause"]; role: "cause" | "effect" }) {
  const displayName = endpoint.label === "Service" ? serviceDisplayName(endpoint.id) : endpoint.id;
  const roleColor = role === "cause" ? SEVERITY_COLOR[endpoint.severity] : "var(--text-faint)";
  return (
    <div
      className="min-w-0 flex-1 rounded-[var(--radius-control)] p-3"
      style={{ border: `1px solid ${role === "cause" ? SEVERITY_COLOR[endpoint.severity] : "var(--border-soft)"}`, background: role === "cause" ? SEVERITY_SOFT[endpoint.severity] : "var(--canvas)" }}
    >
      <div className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.08em]" style={{ color: roleColor }}>
        {role === "cause" ? <Crosshair className="h-3 w-3" strokeWidth={2.5} /> : <Radar className="h-3 w-3" strokeWidth={2.5} />}
        {role === "cause" ? "Root cause" : "Downstream effect"}
      </div>
      <div className="mt-1.5 flex items-center gap-1.5">
        <EndpointGlyph label={endpoint.label} className="h-3.5 w-3.5 flex-shrink-0 text-text-faint" />
        <span className="truncate text-sm font-semibold text-color-text">{displayName}</span>
      </div>
      <div className="mt-1 flex items-center gap-1.5">
        <SeverityBadge severity={endpoint.severity} />
        <span className="truncate text-xs text-text-faint">{metricLabel(endpoint.metric_name)}</span>
      </div>
    </div>
  );
}

/** One "X caused Y" suggestion (basic causal RCA -- see
 * services/api/app/services/rca_suggester.py). Cause and effect render as
 * two distinct blocks joined by a labeled arrow so the direction of
 * causality is legible at a glance, not just readable in a sentence --
 * the API's own `text` field (rendered below the blocks) stays the
 * single source of truth for the exact wording, same reasoning
 * IncidentCard's narrative and buildInsight() already follow. */
function RcaSuggestionRow({ suggestion }: { suggestion: RcaSuggestion }) {
  return (
    <div className="border-b p-4 last:border-b-0" style={{ borderColor: "var(--border-soft)" }}>
      <div className="flex flex-col items-stretch gap-2 sm:flex-row">
        <RcaEndpointBlock endpoint={suggestion.cause} role="cause" />
        <div className="flex flex-shrink-0 flex-row items-center justify-center gap-1.5 py-0.5 sm:flex-col sm:gap-0.5 sm:px-1 sm:py-0">
          <span className="whitespace-nowrap text-[10px] font-medium uppercase tracking-[0.06em] text-text-faint">
            {relationshipLabel(suggestion.relationship)}
          </span>
          <ArrowRight className="h-4 w-4 rotate-90 sm:rotate-0" style={{ color: "var(--accent)" }} strokeWidth={2.5} />
        </div>
        <RcaEndpointBlock endpoint={suggestion.effect} role="effect" />
      </div>
      <p className="mt-2.5 text-sm leading-relaxed text-text-dim">{suggestion.text}</p>
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

function Detail({
  a,
  onClose,
  onResolved,
}: {
  a: AnomalyFlag;
  onClose: () => void;
  onResolved: () => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [isResolving, setIsResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isService = isServiceStateMetric(a.metric_name);
  const service = isService ? parseServiceId(a.hostname) : null;
  // The node an anomalous service actually runs on -- what "View node"
  // should point at instead of the service id itself, which isn't a
  // valid /nodes/{hostname} lookup key.
  const nodeHostname = service?.host ?? a.hostname;

  const resolve = async () => {
    const trimmedNote = note.trim();
    if (!trimmedNote) {
      setResolveError("Add a short resolution note before closing this alert.");
      return;
    }
    setIsResolving(true);
    setResolveError(null);
    try {
      const res = await fetch(`/api/anomalies/${encodeURIComponent(a.hostname)}/${encodeURIComponent(a.metric_name)}/resolve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ note: trimmedNote }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail || "Unable to resolve this alert.");
      await onResolved();
      onClose();
    } catch (err) {
      setResolveError(err instanceof Error ? err.message : "Unable to resolve this alert.");
    } finally {
      setIsResolving(false);
    }
  };

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
            <SeverityBadge severity={a.severity} />
            <h2 className="font-display mt-2 truncate text-lg font-semibold text-color-text">
              {isService ? serviceDisplayName(a.hostname) : a.hostname}
            </h2>
            <div className="mt-0.5 flex items-center gap-1.5 text-sm text-text-faint">
              <MetricGlyph metric={a.metric_name} className="h-3.5 w-3.5" />
              {isService ? `Service · running on ${nodeHostname}` : metricLabel(a.metric_name)}
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

          {isService ? (
            // A service_state flag has no percentage/sigma to show -- it's
            // a live up/down/unreachable check, not a statistical metric
            // (see prometheus_health.py's module docstring). Show what it
            // *is* instead of hiding the section entirely: which service,
            // on which node, and that this came from the OpenStack/
            // Prometheus cross-check rather than the baseline detector.
            <div className="panel overflow-hidden">
              <div className="flex items-center gap-1.5 px-4 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted" style={{ background: "var(--canvas)" }}>
                <Boxes className="h-3.5 w-3.5" strokeWidth={2} />
                Service
              </div>
              <div className="divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                  <span className="text-text-faint">Service</span>
                  <span className="text-color-text">{serviceDisplayName(a.hostname)}</span>
                </div>
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                  <span className="text-text-faint">Running on</span>
                  <Link href={`/topology?highlight=${encodeURIComponent(a.hostname)},${encodeURIComponent(nodeHostname)}`} className="truncate underline" style={{ color: "var(--accent)" }}>
                    {nodeHostname}
                  </Link>
                </div>
                <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                  <span className="text-text-faint">Status</span>
                  <span className="font-medium" style={{ color: SEVERITY_COLOR[a.severity] }}>
                    Not in expected running state
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="panel p-4">
                <div className="eyebrow">Current value</div>
                <div className="stat-figure mt-1 text-2xl text-color-text">{formatMetricValue(a.metric_name, a.current_value)}</div>
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

              {/* Story 3.4: source IPs behind an SSH auth alert -- absent
                 (section not rendered) for metrics that don't carry them
                 (cpu_usage/ram_usage), same "only show what's there"
                 pattern the Service branch above already follows. */}
              {a.details?.source_ips && a.details.source_ips.length > 0 && (
                <div className="panel p-4">
                  <div className="eyebrow">Source IPs</div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {a.details.source_ips.map((ip) => (
                      <span
                        key={ip}
                        className="stat-figure inline-flex items-center rounded px-2 py-1 text-xs font-medium"
                        style={{ color: SEVERITY_COLOR[a.severity], background: SEVERITY_SOFT[a.severity] }}
                      >
                        {ip}
                      </span>
                    ))}
                  </div>
                  {a.details.triggered_by === "absolute_threshold" && (
                    <p className="mt-2 text-xs text-text-faint">
                      Flagged by a fixed-count threshold, independent of this host&apos;s learned baseline.
                    </p>
                  )}
                </div>
              )}
            </>
          )}

          <div className="panel divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
            {[
              isService
                ? { label: "Detection method", value: "Live service state check" }
                : { label: "Detection method", value: METHOD_LABEL[a.method] },
              ...(isService
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

          <div className="panel p-4" style={{ borderColor: "var(--ok)" }}>
            <div className="flex items-center gap-1.5 text-sm font-semibold text-color-text">
              <CheckCircle2 className="h-4 w-4" style={{ color: "var(--ok)" }} strokeWidth={2} />
              Resolve manually
            </div>
            <p className="mt-1 text-xs leading-relaxed text-text-faint">
              Close this alert after intervention. The note is saved in its history; a new alert is raised only after the signal recovers and recurs.
            </p>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              maxLength={2000}
              rows={3}
              placeholder="What did you do, or what should the next operator know?"
              className="mt-3 w-full resize-y rounded-[var(--radius-control)] p-2.5 text-sm text-color-text outline-none transition-colors"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
            />
            {resolveError && <p className="mt-2 text-xs" style={{ color: "var(--crit)" }}>{resolveError}</p>}
            <button
              onClick={resolve}
              disabled={isResolving}
              className="mt-3 inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
              style={{ background: "var(--ok)" }}
            >
              <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2} />
              {isResolving ? "Resolving…" : "Mark as resolved"}
            </button>
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


const NODE_AVATAR_COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

/** Deterministic color per vertex id, off the app's existing 5-color
 * chart palette (see globals.css) rather than a 6th ad hoc scale --
 * gives each incident's root-cause vertex a stable little "avatar" so
 * the same host/service reads as the same color across cards, Notion-
 * board style, without inventing new theme tokens. Plain string hash,
 * not cryptographic -- collisions across a handful of hostnames are a
 * cosmetic non-issue here. */
function nodeAvatarColor(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return NODE_AVATAR_COLORS[Math.abs(hash) % NODE_AVATAR_COLORS.length];
}

function initialsFor(id: string): string {
  const cleaned = id.replace(/@.*/, "").replace(/[-_]/g, " ").trim();
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** "Recent incidents" preview strip -- sits above the full alert/incident
 * feed as a fast-scanning summary, GitHub/Notion-card style: one compact
 * card per correlated incident (member_count > 1 -- a lone alert isn't
 * an "incident" in this sense, same distinction IncidentCard already
 * draws), color-coded by its anchor vertex, most-severe/most-recent
 * first. "View all" jumps straight to the full feed below rather than a
 * separate page -- incidents are computed fresh on every request rather
 * than persisted (see alert_correlation.py's module docstring), so
 * there's no independent incident history to page through yet; the full
 * feed just underneath *is* the complete current picture. */
function RecentIncidentsPreview({
  incidents,
  onOpen,
}: {
  incidents: { incident: AnomalyIncident; members: AnomalyFlag[] }[];
  onOpen: (a: AnomalyFlag) => void;
}) {
  const correlated = useMemo(
    () =>
      incidents
        .filter((g) => g.members.length > 1)
        .slice()
        .sort((a, b) => {
          const rankDiff = SEVERITY_RANK[b.incident.severity] - SEVERITY_RANK[a.incident.severity];
          if (rankDiff !== 0) return rankDiff;
          const latestA = Math.max(...a.members.map((m) => new Date(m.detected_at).getTime()));
          const latestB = Math.max(...b.members.map((m) => new Date(m.detected_at).getTime()));
          return latestB - latestA;
        })
        .slice(0, 3),
    [incidents]
  );

  if (correlated.length === 0) return null;

  const scrollToFeed = () => {
    document.getElementById("incident-feed")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5" style={{ background: "var(--canvas)" }}>
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted">
          <Inbox className="h-3.5 w-3.5" strokeWidth={2} />
          Recent incidents
        </div>
        <button onClick={scrollToFeed} className="text-xs font-semibold underline" style={{ color: "var(--accent)" }}>
          View all ↓
        </button>
      </div>
      <div className="grid gap-2.5 p-3 sm:grid-cols-2 lg:grid-cols-3">
        {correlated.map(({ incident, members }) => {
          const latest = Math.max(...members.map((m) => new Date(m.detected_at).getTime()));
          const anchorId = incident.root_cause_guess?.vertex_id ?? members[0].hostname;
          const anchorLabel = incident.root_cause_guess?.label;
          const displayAnchor = anchorLabel === "Service" ? serviceDisplayName(anchorId) : anchorId;
          const color = nodeAvatarColor(anchorId);
          return (
            <button
              key={incident.incident_id}
              onClick={() => onOpen(members[0])}
              className="panel panel-interactive flex flex-col gap-2 rounded-[var(--radius-panel)] p-3.5 text-left transition-shadow"
              style={{ borderLeft: `3px solid ${SEVERITY_COLOR[incident.severity]}` }}
            >
              <div className="flex items-center gap-2">
                <span
                  className="grid h-6 w-6 flex-shrink-0 place-items-center rounded-full text-[10px] font-bold text-white"
                  style={{ background: color }}
                >
                  {initialsFor(anchorId)}
                </span>
                <span className="truncate text-sm font-semibold text-color-text">{displayAnchor}</span>
                <SeverityBadge severity={incident.severity} />
              </div>
              <p className="line-clamp-2 text-xs leading-relaxed text-text-dim">{incident.narrative}</p>
              <div className="mt-auto flex items-center justify-between text-[11px] text-text-faint">
                <span className="inline-flex items-center gap-1">
                  <Link2 className="h-3 w-3" strokeWidth={2} />
                  {members.length} alerts
                </span>
                <span>{formatRelative(new Date(latest).toISOString())}</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
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

  // Unfiltered incident groups, independent of the search/severity
  // filters below -- the "Recent incidents" preview strip always shows
  // what's actually happening across the whole fleet, not just whatever
  // the alerts list is currently filtered down to.
  const allGroups = useMemo(() => {
    const byIncident = new Map<string, { incident: AnomalyIncident; members: AnomalyFlag[] }>();
    for (const { member, incident } of flatMembers) {
      const existing = byIncident.get(incident.incident_id);
      if (existing) existing.members.push(member);
      else byIncident.set(incident.incident_id, { incident, members: [member] });
    }
    return [...byIncident.values()];
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

      <RecentIncidentsPreview incidents={allGroups} onOpen={(m) => setSelectedKey(flagKey(m))} />

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

      <div id="incident-feed" className="panel overflow-hidden scroll-mt-4">
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

      <AnimatePresence>
        {selected && (
          <Detail
            a={selected}
            onClose={() => setSelectedKey(null)}
            onResolved={async () => {
              await Promise.all([mutate(), mutateRca()]);
            }}
          />
        )}
      </AnimatePresence>
    </main>
  );
}
