"use client";

import { useEffect, useSyncExternalStore } from "react";
import useSWR from "swr";
import type { AnomalyIncident, DashboardNode, LogEntry, ThresholdWarning } from "@/lib/types";
import { parseLevel } from "@/lib/logs";
import type { LogLevel } from "@/lib/types";

/** Throws on non-2xx so SWR exposes `error` (widgets show a quiet "unavailable"
 * state instead of crashing the whole page when one backend is down). */
export async function apiFetcher<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

const live = { refreshInterval: 5000, shouldRetryOnError: false } as const;
const slow = { refreshInterval: 30000, shouldRetryOnError: false } as const;

export const useFleet = () => useSWR<DashboardNode[]>("/api/dashboard", apiFetcher, live);
export const useIncidents = () => useSWR<AnomalyIncident[]>("/api/anomalies/incidents", apiFetcher, slow);
export const useForecastWarnings = () => useSWR<ThresholdWarning[]>("/api/forecast/warnings", apiFetcher, slow);

export type ParsedLog = LogEntry & { level: LogLevel | null };

/** Last hour of logs, newest first, with a level parsed from each line. */
export function useLogs() {
  const swr = useSWR<LogEntry[]>("/api/logs?minutes=60&limit=500", apiFetcher, live);
  const parsed: ParsedLog[] = (swr.data ?? []).map((e) => ({ ...e, level: parseLevel(e.line) }));
  return { ...swr, parsed };
}

export interface TrendPoint {
  ts: number; // unix seconds
  cpu: number;
  mem: number;
}

const LIVE_KEY = "cortex:fleet-trend:v1";
const HOUR = 3600;

// Rolling buffer of live fleet averages, kept in a tiny external store so it
// survives route changes and (via localStorage) reloads.
const NO_SAMPLES: TrendPoint[] = [];
let liveBuf: TrendPoint[] | null = null;
const liveListeners = new Set<() => void>();
const liveSnapshot = () => {
  if (liveBuf === null) {
    liveBuf = NO_SAMPLES;
    try {
      const raw = localStorage.getItem(LIVE_KEY);
      const parsed = raw ? (JSON.parse(raw) as TrendPoint[]) : [];
      const cutoff = Math.floor(Date.now() / 1000) - HOUR;
      if (Array.isArray(parsed)) liveBuf = parsed.filter((p) => p && p.ts > cutoff);
    } catch {
      /* ignore cache errors */
    }
  }
  return liveBuf;
};
const liveSubscribe = (cb: () => void) => {
  liveListeners.add(cb);
  return () => void liveListeners.delete(cb);
};
const pushLive = (sample: TrendPoint) => {
  const prev = liveSnapshot();
  if (prev.length && sample.ts - prev[prev.length - 1].ts < 5) return;
  liveBuf = [...prev.filter((p) => p.ts > sample.ts - HOUR), sample];
  try {
    localStorage.setItem(LIVE_KEY, JSON.stringify(liveBuf));
  } catch {
    /* ignore */
  }
  liveListeners.forEach((cb) => cb());
};

/** Fleet-average CPU / memory over the last hour.
 *
 * Primary source: each node's Prometheus history (same endpoint the node
 * detail page charts), bucketed to 5 minutes and averaged across nodes.
 * Fallback: a rolling buffer of the live fleet averages the dashboard already
 * polls every 5s, persisted in localStorage -- the same trick the node page's
 * useMetricHistory uses, so the widget fills in (and keeps its shape across
 * reloads) even when Prometheus has little or no range data yet, instead of
 * saying "no history" while the node pages clearly have some. */
export function useFleetTrend(nodes: DashboardNode[] | undefined) {
  const instances = (nodes ?? []).map((n) => n.instance).filter(Boolean).sort();
  const hist = useSWR<TrendPoint[]>(
    instances.length ? ["fleet-trend", instances.join(",")] : null,
    async () => {
      const results = await Promise.all(
        instances.map((i) =>
          apiFetcher<Record<string, { t: number; v: number }[]>>(`/api/nodes/${encodeURIComponent(i)}/history?minutes=60`).catch(() => null),
        ),
      );
      if (results.every((r) => r === null)) throw new Error("history unavailable");
      const buckets = new Map<number, { cpu: number[]; mem: number[] }>();
      for (const r of results) {
        if (!r) continue;
        (["cpu_percent", "memory_percent"] as const).forEach((metric) => {
          for (const p of r[metric] ?? []) {
            const b = Math.floor(p.t / 300) * 300;
            const slot = buckets.get(b) ?? { cpu: [], mem: [] };
            slot[metric === "cpu_percent" ? "cpu" : "mem"].push(p.v);
            buckets.set(b, slot);
          }
        });
      }
      const avg = (a: number[]) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0);
      return Array.from(buckets.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([ts, v]) => ({ ts, cpu: Math.round(avg(v.cpu) * 10) / 10, mem: Math.round(avg(v.mem) * 10) / 10 }));
    },
    { refreshInterval: 60000, shouldRetryOnError: false },
  );

  const live = useSyncExternalStore(liveSubscribe, liveSnapshot, () => NO_SAMPLES);

  // Append one sample per fleet refresh.
  useEffect(() => {
    const withMetrics = (nodes ?? []).filter((n) => n.metrics);
    if (!withMetrics.length) return;
    const mean = (key: "cpu_percent" | "memory_percent") =>
      Math.round((withMetrics.reduce((s, n) => s + (n.metrics?.[key] ?? 0), 0) / withMetrics.length) * 10) / 10;
    pushLive({ ts: Math.floor(Date.now() / 1000), cpu: mean("cpu_percent"), mem: mean("memory_percent") });
  }, [nodes]);

  const prom = hist.data ?? [];
  const data = prom.length >= 2 ? prom : live;
  return {
    data,
    source: prom.length >= 2 ? ("prometheus" as const) : ("live" as const),
    // Only an error when there is truly nothing to draw.
    error: data.length ? undefined : hist.error,
    isLoading: hist.isLoading && live.length === 0,
  };
}

export function copilotHref(prompt: string) {
  return `/copilot?investigate=${encodeURIComponent(prompt)}`;
}
