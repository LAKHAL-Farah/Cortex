import type { QuotaAlert, QuotaBreachType, QuotaResource, QuotaSeverity } from "./types";

export { formatRelative } from "./anomalies";

export const ALL_QUOTA_SEVERITIES: QuotaSeverity[] = ["critical", "warning"];

export const QUOTA_SEVERITY_COLOR: Record<QuotaSeverity, string> = {
  critical: "var(--crit)",
  warning: "var(--warn)",
  normal: "var(--ok)",
};

export const QUOTA_SEVERITY_SOFT: Record<QuotaSeverity, string> = {
  critical: "var(--crit-soft)",
  warning: "var(--warn-soft)",
  normal: "var(--ok-soft)",
};

export const QUOTA_SEVERITY_LABEL: Record<QuotaSeverity, string> = {
  critical: "Critical",
  warning: "Warning",
  normal: "Normal",
};

// Deliberately a *different* palette dimension than severity (which already
// owns warn/crit/ok) -- this distinguishes *which kind* of cap a row is,
// independent of how close it is to breaching it, so "capacity" vs "budget"
// stays visually identifiable even scanning a mixed, multi-severity list.
export const BREACH_TYPE_COLOR: Record<QuotaBreachType, string> = {
  capacity_cap: "var(--chart-2)", // blue -- infrastructure/quota
  budget_cap: "var(--chart-4)", // purple -- cost/spend
};

export const BREACH_TYPE_LABEL: Record<QuotaBreachType, string> = {
  capacity_cap: "Capacity cap",
  budget_cap: "Budget cap",
};

// One line under each type's label, reused across the summary cards and the
// filter control so the "capacity cap != budget cap" distinction from
// models.QuotaAlert's docstring is visible in the UI too, not just in the
// alert message text.
export const BREACH_TYPE_DESCRIPTION: Record<QuotaBreachType, string> = {
  capacity_cap: "OpenStack quota limit — raising it takes an admin action, not more budget.",
  budget_cap: "Estimated spend ceiling — quota headroom may still be available.",
};

const RESOURCE_LABEL: Record<QuotaResource, string> = {
  instances: "VM instances",
  vcpus: "vCPUs",
  ram_mb: "RAM",
  floating_ips: "Floating IPs",
  volumes: "Volumes",
  gigabytes: "Volume storage",
  estimated_cost_eur: "Estimated monthly cost",
};

export function resourceLabel(resource: QuotaResource | string): string {
  return RESOURCE_LABEL[resource as QuotaResource] ?? resource.replace(/_/g, " ");
}

/** Formats `used`/`limit` in whatever unit fits the resource -- raw counts
 * for instances/vcpus/floating_ips/volumes, GB for ram_mb/gigabytes (ram_mb
 * is stored in MB, same unit Nova's `GET /limits` reports), and EUR for the
 * budget_cap row. Keeps the numeric formatting decision in one place rather
 * than scattered across the card/table/detail views. */
export function formatQuotaValue(alert: QuotaAlert): { used: string; limit: string } {
  if (alert.resource === "estimated_cost_eur") {
    return { used: `€${alert.used.toFixed(2)}`, limit: `€${alert.limit.toFixed(2)}` };
  }
  if (alert.resource === "ram_mb") {
    return { used: `${(alert.used / 1024).toFixed(1)} GB`, limit: `${(alert.limit / 1024).toFixed(1)} GB` };
  }
  if (alert.resource === "gigabytes") {
    return { used: `${alert.used.toFixed(0)} GB`, limit: `${alert.limit.toFixed(0)} GB` };
  }
  return { used: alert.used.toFixed(0), limit: alert.limit.toFixed(0) };
}

export function formatRatio(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/** 0..100 fill for a ProgressBar, clamped so a ratio slightly over 1.0 (a
 * budget/quota that's since been exceeded, not just reached) doesn't blow
 * out the bar's width. */
export function quotaFill(ratio: number): number {
  return Math.min(Math.max(ratio, 0), 1) * 100;
}

/** Group a flat alert list by project_id, in encounter order -- backs the
 * "By project" view, which is the more actionable read for an operator
 * ("what does proj-X need") versus a flat severity-sorted list. */
export function groupByProject(alerts: QuotaAlert[]): Map<string, QuotaAlert[]> {
  const grouped = new Map<string, QuotaAlert[]>();
  for (const alert of alerts) {
    const existing = grouped.get(alert.project_id);
    if (existing) {
      existing.push(alert);
    } else {
      grouped.set(alert.project_id, [alert]);
    }
  }
  return grouped;
}
