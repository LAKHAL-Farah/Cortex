"use client";

import React from "react";
import useSWR from "swr";
import type { AnomalyIncident, QuotaAlert, ThresholdWarning } from "@/lib/types";
import { SEVERITY_LABEL, serviceDisplayName, metricLabel as anomalyMetricLabel } from "@/lib/anomalies";
import { metricLabel as forecastMetricLabel, thresholdEtaLabel } from "@/lib/thresholds";
import { resourceLabel } from "@/lib/quotas";

export type NotificationLevel = "critical" | "warning" | "info";
export type NotificationKind = "incident" | "quota" | "forecast";

export interface AppNotification {
  id: string;
  kind: NotificationKind;
  level: NotificationLevel;
  title: string;
  detail: string;
  href: string;
  /** ISO timestamp when known, used for "5m ago" labels. */
  at?: string;
}

const READ_KEY = "cortex-notifications-read";
const POLL_MS = 30_000;

const listFetcher = async <T,>(url: string): Promise<T[]> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return [];
  const data = await res.json().catch(() => []);
  return Array.isArray(data) ? (data as T[]) : [];
};

function readStoredIds(): Set<string> {
  try {
    const raw = localStorage.getItem(READ_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

function writeStoredIds(ids: Set<string>) {
  try {
    // Cap the list so it can't grow forever as old alerts come and go.
    localStorage.setItem(READ_KEY, JSON.stringify(Array.from(ids).slice(-300)));
  } catch {}
}

function incidentToNotification(i: AnomalyIncident): AppNotification {
  const rootId = i.root_cause_guess?.vertex_id;
  const first = i.members?.[0];
  let subject = rootId ?? first?.hostname ?? "Unknown source";
  if (rootId) subject = serviceDisplayName(rootId);
  const metrics = Array.from(new Set((i.members ?? []).map((m) => anomalyMetricLabel(m.metric_name)))).slice(0, 2);
  return {
    id: `incident:${i.incident_id}`,
    kind: "incident",
    level: i.severity === "critical" ? "critical" : "warning",
    title: `${SEVERITY_LABEL[i.severity]} incident · ${subject}`,
    detail: `${i.member_count} correlated alert${i.member_count === 1 ? "" : "s"}${metrics.length ? ` · ${metrics.join(", ")}` : ""}`,
    href: "/alerts",
    at: first?.detected_at,
  };
}

function quotaToNotification(a: QuotaAlert): AppNotification {
  const kind = a.breach_type === "budget_cap" ? "Budget" : "Quota";
  return {
    id: `quota:${a.project_id}:${a.breach_type}:${a.resource}:${a.severity}`,
    kind: "quota",
    level: a.severity === "critical" ? "critical" : "warning",
    title: `${kind} ${a.severity} · ${a.project_name}`,
    detail: a.message ?? `${resourceLabel(a.resource)} at ${Math.round(a.ratio * 100)}% of limit`,
    href: "/quotas",
    at: a.detected_at,
  };
}

function forecastToNotification(w: ThresholdWarning): AppNotification {
  return {
    id: `forecast:${w.hostname}:${w.metric}:${w.already_breached ? "now" : "soon"}`,
    kind: "forecast",
    level: w.already_breached ? "critical" : "info",
    title: `${forecastMetricLabel(w.metric)} · ${w.hostname}`,
    detail: `Projected to hit ${w.threshold}% ${thresholdEtaLabel(w)}`,
    href: "/forecast",
    at: w.crossing_timestamp ?? undefined,
  };
}

const LEVEL_RANK: Record<NotificationLevel, number> = { critical: 0, warning: 1, info: 2 };

export function useNotifications() {
  const opts = { refreshInterval: POLL_MS, revalidateOnFocus: true, shouldRetryOnError: false } as const;
  const incidents = useSWR<AnomalyIncident[]>("/api/anomalies/incidents", listFetcher, opts);
  const quotas = useSWR<QuotaAlert[]>("/api/quotas/alerts", listFetcher, opts);
  const forecast = useSWR<ThresholdWarning[]>("/api/forecast/warnings", listFetcher, opts);

  const [readIds, setReadIds] = React.useState<Set<string>>(() => readStoredIds());

  // The header's refresh button should also refresh the bell.
  const { mutate: mIncidents } = incidents;
  const { mutate: mQuotas } = quotas;
  const { mutate: mForecast } = forecast;
  React.useEffect(() => {
    const onRefresh = () => {
      mIncidents();
      mQuotas();
      mForecast();
    };
    window.addEventListener("cortex:refresh", onRefresh);
    return () => window.removeEventListener("cortex:refresh", onRefresh);
  }, [mIncidents, mQuotas, mForecast]);

  const items = React.useMemo<AppNotification[]>(() => {
    const out: AppNotification[] = [
      ...(incidents.data ?? []).map(incidentToNotification),
      ...(quotas.data ?? []).filter((a) => a.severity !== "normal").map(quotaToNotification),
      ...(forecast.data ?? []).filter((w) => w.will_breach || w.already_breached).map(forecastToNotification),
    ];
    return out.sort((a, b) => LEVEL_RANK[a.level] - LEVEL_RANK[b.level]);
  }, [incidents.data, quotas.data, forecast.data]);

  const unreadCount = items.filter((n) => !readIds.has(n.id)).length;

  const markRead = React.useCallback((id: string) => {
    setReadIds((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev).add(id);
      writeStoredIds(next);
      return next;
    });
  }, []);

  const markAllRead = React.useCallback(() => {
    setReadIds((prev) => {
      const next = new Set(prev);
      items.forEach((n) => next.add(n.id));
      writeStoredIds(next);
      return next;
    });
  }, [items]);

  return {
    items,
    readIds,
    unreadCount,
    markRead,
    markAllRead,
    loading: incidents.isLoading || quotas.isLoading || forecast.isLoading,
  };
}
