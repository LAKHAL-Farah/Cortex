"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { TrendingUp, RefreshCw, Sparkles, Server, Cpu, MemoryStick, HardDrive, Activity, CalendarClock } from "lucide-react";
import type { ForecastResult, DashboardNode } from "@/lib/types";
import {
  LineChart,
  Line,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const FORECAST_METRICS = ["cpu_percent", "memory_percent", "disk_percent"] as const;

const DAY_LABELS: Record<string, string> = {
  tomorrow: "Tomorrow",
  "7_days": "In 7 days",
  "30_days": "In 30 days",
};

function metricLabel(metric: string): string {
  switch (metric) {
    case "cpu_percent":
      return "CPU usage";
    case "memory_percent":
      return "Memory usage";
    case "disk_percent":
      return "Disk usage";
    default:
      return metric;
  }
}

function MetricGlyph({ metric, className }: { metric: string; className?: string }) {
  switch (metric) {
    case "cpu_percent":
      return <Cpu className={className} strokeWidth={1.75} />;
    case "memory_percent":
      return <MemoryStick className={className} strokeWidth={1.75} />;
    case "disk_percent":
      return <HardDrive className={className} strokeWidth={1.75} />;
    default:
      return <Activity className={className} strokeWidth={1.75} />;
  }
}

function StatCard({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: typeof Server;
  label: string;
  value: React.ReactNode;
  color: string;
}) {
  return (
    <div className="panel flex items-center gap-3 p-4">
      <div
        className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
        style={{ background: `color-mix(in srgb, ${color} 12%, transparent)` }}
      >
        <Icon className="h-4.5 w-4.5" style={{ color }} strokeWidth={1.75} />
      </div>
      <div className="min-w-0">
        <div className="stat-figure text-xl text-color-text">{value}</div>
        <div className="truncate text-xs text-text-faint">{label}</div>
      </div>
    </div>
  );
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as ForecastResult;
};

const nodesFetcher = (url: string) => fetch(url).then((r) => r.json());

const selectClass = "rounded-[var(--radius-control)] px-3 py-2 text-sm text-color-text outline-none transition-colors";
const selectStyle = { border: "1px solid var(--border)", background: "var(--canvas)" } as const;

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: { label: string; value: number } }>;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div className="panel p-3.5 text-sm text-color-text" style={{ boxShadow: "var(--shadow-hover)" }}>
      <div className="eyebrow">{p.label}</div>
      <div className="mt-2 flex items-center gap-2.5">
        <span className="status-dot" style={{ background: "var(--chart-1)" }} />
        <span className="font-medium">Forecast</span>{" "}
        <span className="stat-figure text-text-faint">{p.value.toFixed(1)}%</span>
      </div>
    </div>
  );
}

export default function ForecastExplorer() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const { data: nodes } = useSWR<DashboardNode[]>("/api/dashboard", nodesFetcher, { refreshInterval: 15000 });
  const hostnames = useMemo(() => (nodes ?? []).map((n) => n.hostname).sort(), [nodes]);

  const [host, setHost] = useState<string>(searchParams.get("host") ?? "");
  const [metric, setMetric] = useState<string>(searchParams.get("metric") ?? FORECAST_METRICS[0]);

  const effectiveHost = host && hostnames.includes(host) ? host : hostnames[0] ?? "";

  useEffect(() => {
    if (!effectiveHost) return;
    const params = new URLSearchParams({ host: effectiveHost, metric });
    router.replace(`/forecast?${params.toString()}`, { scroll: false });
  }, [effectiveHost, metric, router]);

  const {
    data: result,
    error,
    isLoading,
    isValidating,
    mutate,
  } = useSWR<ForecastResult>(
    effectiveHost ? `/api/forecast/${encodeURIComponent(effectiveHost)}/${encodeURIComponent(metric)}` : null,
    fetcher
  );

  const points = useMemo(() => result?.forecast ?? [], [result]);
  const chartData = points.map((p) => ({
    day: p.day,
    label: DAY_LABELS[p.day] ?? p.day,
    value: p.value,
  }));

  const tomorrow = points.find((p) => p.day === "tomorrow");
  const in7 = points.find((p) => p.day === "7_days");
  const in30 = points.find((p) => p.day === "30_days");
  const trendUp = tomorrow && in30 ? in30.value - tomorrow.value : null;

  return (
    <main className="grid gap-4">
      <div className="glow-surface panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <TrendingUp className="h-4.5 w-4.5 text-text-dim" strokeWidth={1.75} />
          </div>
          <div>
            <div className="eyebrow">Monitoring</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Usage Forecast</h1>
            <div className="mt-0.5 text-sm text-text-faint">Projected usage per node &amp; metric</div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select value={effectiveHost} onChange={(e) => setHost(e.target.value)} className={selectClass} style={selectStyle}>
            {!hostnames.length && <option value="">No nodes</option>}
            {hostnames.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
          <select value={metric} onChange={(e) => setMetric(e.target.value)} className={selectClass} style={selectStyle}>
            {FORECAST_METRICS.map((m) => (
              <option key={m} value={m}>
                {metricLabel(m)}
              </option>
            ))}
          </select>
          <button
            onClick={() => mutate()}
            aria-label="Refresh forecast"
            className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard icon={CalendarClock} label="Tomorrow" value={tomorrow ? `${tomorrow.value.toFixed(1)}%` : "—"} color="var(--chart-1)" />
        <StatCard icon={CalendarClock} label="In 7 days" value={in7 ? `${in7.value.toFixed(1)}%` : "—"} color="var(--chart-3)" />
        <StatCard icon={CalendarClock} label="In 30 days" value={in30 ? `${in30.value.toFixed(1)}%` : "—"} color="var(--chart-4)" />
        <StatCard
          icon={TrendingUp}
          label="Trend (tomorrow → 30d)"
          value={trendUp !== null ? `${trendUp >= 0 ? "+" : ""}${trendUp.toFixed(1)} pts` : "—"}
          color={trendUp !== null && trendUp > 0 ? "var(--crit)" : "var(--accent)"}
        />
      </div>

      {!isLoading && !error && !!points.length && (
        <div className="glow-insight rounded-[var(--radius-panel)] p-4">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--accent)" }}>
            <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
            Generated insight
          </div>
          <p className="mt-2 text-sm leading-relaxed text-color-text">
            {trendUp !== null
              ? trendUp > 0
                ? `${effectiveHost || "This node"}'s ${metricLabel(metric).toLowerCase()} is projected to rise by ${trendUp.toFixed(1)} points over the next 30 days.`
                : `${effectiveHost || "This node"}'s ${metricLabel(metric).toLowerCase()} is projected to stay flat or decline over the next 30 days.`
              : "Not enough forecast points to compute a trend yet."}
          </p>
        </div>
      )}

      {error && (
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          Couldn&apos;t load this forecast: {error.message}
        </div>
      )}

      {isLoading && <p className="p-2 text-sm text-text-faint">Loading forecast…</p>}

      {!isLoading && !error && !points.length && (
        <div className="panel p-6 text-sm text-text-faint">
          No forecast available yet for <span className="text-color-text">{effectiveHost}</span> / {metricLabel(metric)}.
        </div>
      )}

      {!!points.length && (
        <section className="panel p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <div className="eyebrow">Projection</div>
              <h3 className="mt-1 flex items-center gap-1.5 text-[15px] font-semibold text-color-text">
                <MetricGlyph metric={metric} className="h-4 w-4 text-text-faint" />
                {metricLabel(metric)} forecast
              </h3>
            </div>
          </div>

          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
              <YAxis axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} domain={[0, 100]} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--accent)", strokeWidth: 1, strokeDasharray: "4 4" }} />
              <Line
                type="monotone"
                dataKey="value"
                stroke="var(--chart-1)"
                strokeWidth={2}
                dot={{ r: 4, stroke: "var(--chart-1)", strokeWidth: 2, fill: "#fff" }}
                activeDot={{ r: 5, stroke: "var(--chart-1)", strokeWidth: 2, fill: "#fff" }}
              />
            </LineChart>
          </ResponsiveContainer>
        </section>
      )}
    </main>
  );
}
