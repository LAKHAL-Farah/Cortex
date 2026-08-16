"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Wallet,
  Gauge,
  Search,
  AlertTriangle,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
  Layers,
  Rows3,
  Info,
} from "lucide-react";
import type { QuotaAlert, QuotaBreachType, QuotaSeverity } from "@/lib/types";
import {
  ALL_QUOTA_SEVERITIES,
  BREACH_TYPE_COLOR,
  BREACH_TYPE_DESCRIPTION,
  BREACH_TYPE_LABEL,
  QUOTA_SEVERITY_COLOR,
  QUOTA_SEVERITY_LABEL,
  QUOTA_SEVERITY_SOFT,
  formatQuotaValue,
  formatRatio,
  formatRelative,
  groupByProject,
  quotaFill,
  resourceLabel,
} from "@/lib/quotas";
import { Card } from "./ui/Card";
import { ProgressBar } from "./ui/ProgressBar";
import QuotaResyncButton from "./QuotaResyncButton";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as QuotaAlert[];
};

const SEVERITY_RANK: Record<QuotaSeverity, number> = { critical: 2, warning: 1, normal: 0 };

function SeverityBadge({ severity }: { severity: QuotaSeverity }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ color: QUOTA_SEVERITY_COLOR[severity], background: QUOTA_SEVERITY_SOFT[severity] }}
    >
      <span className="status-dot" style={{ background: QUOTA_SEVERITY_COLOR[severity] }} />
      {QUOTA_SEVERITY_LABEL[severity]}
    </span>
  );
}

function BreachTypeBadge({ type }: { type: QuotaBreachType }) {
  const Icon = type === "capacity_cap" ? Gauge : Wallet;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ color: BREACH_TYPE_COLOR[type], background: `color-mix(in srgb, ${BREACH_TYPE_COLOR[type]} 12%, transparent)` }}
    >
      <Icon className="h-3 w-3" strokeWidth={2} />
      {BREACH_TYPE_LABEL[type]}
    </span>
  );
}

/** Summary tile for one metric (e.g. "Critical", "Capacity breaches") --
 * simpler than the dashboard's MetricCard (no trend/sparkline, this is a
 * live count, not a time series), but same Card + icon-badge language. */
function SummaryTile({
  label,
  value,
  color,
  icon: Icon,
}: {
  label: string;
  value: number;
  color: string;
  icon: typeof Wallet;
}) {
  return (
    <Card interactive className="flex items-center gap-3">
      <span
        className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
        style={{ background: `color-mix(in srgb, ${color} 14%, transparent)` }}
      >
        <Icon className="h-4 w-4" style={{ color }} strokeWidth={1.75} />
      </span>
      <div>
        <div className="stat-figure text-[22px] text-color-text">{value}</div>
        <div className="text-xs text-text-faint">{label}</div>
      </div>
    </Card>
  );
}

function QuotaAlertRow({ alert }: { alert: QuotaAlert }) {
  const { used, limit } = formatQuotaValue(alert);
  return (
    <div
      className="rounded-[var(--radius-panel)] border p-4"
      style={{ borderColor: "var(--border-soft)", borderLeft: `3px solid ${QUOTA_SEVERITY_COLOR[alert.severity]}` }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={alert.severity} />
        <BreachTypeBadge type={alert.breach_type} />
        <span className="ml-auto text-xs text-text-faint">{formatRelative(alert.detected_at)}</span>
      </div>

      <div className="mt-2.5 flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-color-text">{alert.project_name}</div>
          <div className="truncate text-xs text-text-faint">{resourceLabel(alert.resource)}</div>
        </div>
        <div className="flex-shrink-0 text-right text-sm font-medium text-color-text">
          {used} <span className="text-text-faint">/ {limit}</span>
        </div>
      </div>

      <div className="mt-2">
        <ProgressBar value={quotaFill(alert.ratio)} color={QUOTA_SEVERITY_COLOR[alert.severity]} />
        <div className="mt-1 text-right text-xs text-text-faint">{formatRatio(alert.ratio)} of cap</div>
      </div>

      {alert.message && (
        <p className="mt-2.5 text-sm leading-relaxed text-text-dim">{alert.message}</p>
      )}
    </div>
  );
}

/** Per-project card for the "By project" grouping -- every one of that
 * project's breaching slots stacked together, so an operator scanning by
 * project sees "proj-X is over on vCPUs AND over budget" in one place
 * instead of scattered across a severity-sorted flat list. */
function ProjectGroupCard({ projectId, alerts }: { projectId: string; alerts: QuotaAlert[] }) {
  const [expanded, setExpanded] = useState(true);
  const projectName = alerts[0]?.project_name ?? projectId;
  const worst = alerts.reduce<QuotaSeverity>(
    (acc, a) => (SEVERITY_RANK[a.severity] > SEVERITY_RANK[acc] ? a.severity : acc),
    "normal"
  );
  const hasCapacity = alerts.some((a) => a.breach_type === "capacity_cap");
  const hasBudget = alerts.some((a) => a.breach_type === "budget_cap");

  return (
    <Card padding="p-0" className="overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-3 p-4 text-left"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 flex-shrink-0 text-text-faint" strokeWidth={2} />
        ) : (
          <ChevronRight className="h-4 w-4 flex-shrink-0 text-text-faint" strokeWidth={2} />
        )}
        <span
          className="h-2 w-2 flex-shrink-0 rounded-full"
          style={{ background: QUOTA_SEVERITY_COLOR[worst] }}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-color-text">{projectName}</div>
          <div className="truncate text-xs text-text-faint">{projectId}</div>
        </div>
        <div className="flex flex-shrink-0 items-center gap-1.5">
          {hasCapacity && <BreachTypeBadge type="capacity_cap" />}
          {hasBudget && <BreachTypeBadge type="budget_cap" />}
        </div>
        <span className="flex-shrink-0 text-xs text-text-faint">
          {alerts.length} breach{alerts.length === 1 ? "" : "es"}
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 border-t p-3" style={{ borderColor: "var(--border-soft)" }}>
          {alerts.map((a) => {
            const { used, limit } = formatQuotaValue(a);
            return (
              <div key={`${a.breach_type}-${a.resource}`} className="flex items-center gap-3 py-1">
                <span
                  className="h-1.5 w-1.5 flex-shrink-0 rounded-full"
                  style={{ background: BREACH_TYPE_COLOR[a.breach_type] }}
                />
                <span className="w-36 flex-shrink-0 truncate text-xs text-text-dim">{resourceLabel(a.resource)}</span>
                <div className="flex-1">
                  <ProgressBar value={quotaFill(a.ratio)} color={QUOTA_SEVERITY_COLOR[a.severity]} />
                </div>
                <span className="w-28 flex-shrink-0 text-right text-xs text-text-faint">
                  {used} / {limit}
                </span>
                <span className="w-10 flex-shrink-0 text-right text-xs font-medium" style={{ color: QUOTA_SEVERITY_COLOR[a.severity] }}>
                  {formatRatio(a.ratio)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

type ViewMode = "list" | "project";

export default function QuotaBudgetView() {
  const { data, error, isLoading, isValidating } = useSWR<QuotaAlert[]>("/api/quotas/alerts", fetcher, {
    refreshInterval: 10000,
    keepPreviousData: true,
  });

  const [search, setSearch] = useState("");
  const [severityFilter, setSeverityFilter] = useState<QuotaSeverity | "all">("all");
  const [typeFilter, setTypeFilter] = useState<QuotaBreachType | "all">("all");
  const [view, setView] = useState<ViewMode>("list");

  const alerts = useMemo(() => data ?? [], [data]);

  const counts = useMemo(() => {
    const critical = alerts.filter((a) => a.severity === "critical").length;
    const warning = alerts.filter((a) => a.severity === "warning").length;
    const capacity = alerts.filter((a) => a.breach_type === "capacity_cap").length;
    const budget = alerts.filter((a) => a.breach_type === "budget_cap").length;
    return { critical, warning, capacity, budget };
  }, [alerts]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return alerts
      .filter((a) => severityFilter === "all" || a.severity === severityFilter)
      .filter((a) => typeFilter === "all" || a.breach_type === typeFilter)
      .filter((a) => !q || a.project_name.toLowerCase().includes(q) || a.project_id.toLowerCase().includes(q))
      .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || b.ratio - a.ratio);
  }, [alerts, search, severityFilter, typeFilter]);

  const grouped = useMemo(() => Array.from(groupByProject(filtered).entries()), [filtered]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-[22px] font-semibold text-color-text">Quotas &amp; Budget</h1>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            OpenStack quota (<span style={{ color: BREACH_TYPE_COLOR.capacity_cap }}>capacity cap</span>) and
            estimated-spend (<span style={{ color: BREACH_TYPE_COLOR.budget_cap }}>budget cap</span>) breaches, per
            project — kept separate from Alerts, which tracks host-level resource-usage anomalies instead.
          </p>
        </div>
        <QuotaResyncButton />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryTile label="Critical breaches" value={counts.critical} color="var(--crit)" icon={AlertTriangle} />
        <SummaryTile label="Warnings" value={counts.warning} color="var(--warn)" icon={AlertTriangle} />
        <SummaryTile label="Capacity cap" value={counts.capacity} color={BREACH_TYPE_COLOR.capacity_cap} icon={Gauge} />
        <SummaryTile label="Budget cap" value={counts.budget} color={BREACH_TYPE_COLOR.budget_cap} icon={Wallet} />
      </div>

      <Card padding="p-3" className="flex items-start gap-2.5">
        <Info className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "var(--text-muted)" }} strokeWidth={1.75} />
        <div className="grid gap-1.5 text-xs text-text-faint sm:grid-cols-2">
          <div>
            <span className="font-medium" style={{ color: BREACH_TYPE_COLOR.capacity_cap }}>
              {BREACH_TYPE_LABEL.capacity_cap}:
            </span>{" "}
            {BREACH_TYPE_DESCRIPTION.capacity_cap}
          </div>
          <div>
            <span className="font-medium" style={{ color: BREACH_TYPE_COLOR.budget_cap }}>
              {BREACH_TYPE_LABEL.budget_cap}:
            </span>{" "}
            {BREACH_TYPE_DESCRIPTION.budget_cap}
          </div>
        </div>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-faint" strokeWidth={2} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by project…"
            className="h-9 w-full rounded-[var(--radius-control)] border bg-transparent pl-8 pr-3 text-sm text-color-text outline-none"
            style={{ borderColor: "var(--border)" }}
          />
        </div>

        <div className="flex items-center gap-1 rounded-[var(--radius-control)] border p-0.5" style={{ borderColor: "var(--border)" }}>
          {(["all", ...ALL_QUOTA_SEVERITIES] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSeverityFilter(s)}
              className="rounded-[calc(var(--radius-control)-2px)] px-2.5 py-1 text-xs font-medium transition-colors"
              style={{
                color: severityFilter === s ? "var(--text)" : "var(--text-faint)",
                background: severityFilter === s ? "var(--accent-soft)" : "transparent",
              }}
            >
              {s === "all" ? "All severities" : QUOTA_SEVERITY_LABEL[s]}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 rounded-[var(--radius-control)] border p-0.5" style={{ borderColor: "var(--border)" }}>
          {(["all", "capacity_cap", "budget_cap"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTypeFilter(t)}
              className="rounded-[calc(var(--radius-control)-2px)] px-2.5 py-1 text-xs font-medium transition-colors"
              style={{
                color: typeFilter === t ? "var(--text)" : "var(--text-faint)",
                background: typeFilter === t ? "var(--accent-soft)" : "transparent",
              }}
            >
              {t === "all" ? "All types" : BREACH_TYPE_LABEL[t]}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-1 rounded-[var(--radius-control)] border p-0.5" style={{ borderColor: "var(--border)" }}>
          <button
            onClick={() => setView("list")}
            title="Flat list"
            className="rounded-[calc(var(--radius-control)-2px)] p-1.5"
            style={{ background: view === "list" ? "var(--accent-soft)" : "transparent", color: view === "list" ? "var(--accent)" : "var(--text-faint)" }}
          >
            <Rows3 className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
          <button
            onClick={() => setView("project")}
            title="Group by project"
            className="rounded-[calc(var(--radius-control)-2px)] p-1.5"
            style={{ background: view === "project" ? "var(--accent-soft)" : "transparent", color: view === "project" ? "var(--accent)" : "var(--text-faint)" }}
          >
            <Layers className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        </div>
      </div>

      <div className="flex items-center justify-between text-xs text-text-faint">
        <span>
          {isLoading ? "Loading…" : error ? "—" : `${filtered.length} breach${filtered.length === 1 ? "" : "es"}`}
          {!isLoading && !error ? " · live" : ""}
        </span>
        {isValidating && !isLoading && <span>Refreshing…</span>}
      </div>

      {error && (
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          <AlertTriangle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
          <span style={{ color: "var(--crit)" }}>Couldn&apos;t reach the quota service: {error.message}</span>
        </div>
      )}

      {isLoading && <p className="p-6 text-sm text-text-faint">Loading…</p>}

      {!isLoading && !error && filtered.length === 0 && (
        <div className="flex flex-col items-center gap-2 p-10 text-center">
          <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
          <p className="text-sm text-text-faint">
            {alerts.length === 0
              ? "No quota or budget breaches — every project is within its capacity and budget caps."
              : "No breaches match these filters."}
          </p>
        </div>
      )}

      {!isLoading && !error && filtered.length > 0 && view === "list" && (
        <div className="grid gap-2.5 sm:grid-cols-2">
          {filtered.map((a) => (
            <QuotaAlertRow key={`${a.project_id}-${a.breach_type}-${a.resource}`} alert={a} />
          ))}
        </div>
      )}

      {!isLoading && !error && filtered.length > 0 && view === "project" && (
        <div className="space-y-2.5">
          {grouped.map(([projectId, projectAlerts]) => (
            <ProjectGroupCard key={projectId} projectId={projectId} alerts={projectAlerts} />
          ))}
        </div>
      )}
    </div>
  );
}
