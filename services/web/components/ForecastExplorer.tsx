"use client";

import { useMemo, useState, useEffect, useId } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  TrendingUp,
  RefreshCw,
  Sparkles,
  Server,
  Cpu,
  MemoryStick,
  HardDrive,
  Activity,
  Gauge,
  CalendarClock,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { ForecastResult, DashboardNode } from "@/lib/types";
import {
  ComposedChart,
  Area,
  Line,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Brush,
  ReferenceLine,
} from "recharts";

const FORECAST_METRICS = ["cpu_percent", "memory_percent", "disk_percent"] as const;

const MODEL_TYPE_LABELS: Record<string, string> = {
  ml_quantile: "ML model (pooled, quantile regression)",
  fallback_seasonal_persistence: "Fallback model (limited history)",
};

// Palette note: deliberately avoids --chart-3 (green) across this whole page —
// "actual" uses the blue and "predicted" uses the orange/red so the two
// series stay readable against each other without reaching for green at all.
const COLOR_ACTUAL = "var(--chart-2)";
const COLOR_PREDICTED = "var(--chart-1)";
const COLOR_MODEL_ML = "var(--chart-4)";
const COLOR_MODEL_FALLBACK = "#d97706";

const ZOOM_PRESETS = [
  { label: "24h", hoursBack: 6, hoursForward: 24 },
  { label: "3d", hoursBack: 24, hoursForward: 72 },
  { label: "7d", hoursBack: 48, hoursForward: 168 },
  { label: "All", hoursBack: null, hoursForward: null },
] as const;

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
    <div className="panel flex items-center gap-3 p-4 transition-shadow hover:shadow-[var(--shadow-hover)]">
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

function formatTick(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric" });
}

type ChartRow = {
  timestamp: string;
  ts: number;
  label: string;
  actual?: number;
  predicted?: number;
  lower?: number;
  upper?: number;
  band?: number;
};

function ChartTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ChartRow }> }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div className="panel p-3.5 text-sm text-color-text" style={{ boxShadow: "var(--shadow-hover)" }}>
      <div className="eyebrow">{p.label}</div>
      <div className="mt-2 space-y-1.5">
        {p.actual !== undefined && (
          <div className="flex items-center gap-2.5">
            <span className="status-dot" style={{ background: COLOR_ACTUAL }} />
            <span className="font-medium">Actual</span>
            <span className="stat-figure text-text-faint">{p.actual.toFixed(1)}%</span>
          </div>
        )}
        {p.predicted !== undefined && (
          <div className="flex items-center gap-2.5">
            <span className="status-dot" style={{ background: COLOR_PREDICTED }} />
            <span className="font-medium">Predicted</span>
            <span className="stat-figure text-text-faint">{p.predicted.toFixed(1)}%</span>
          </div>
        )}
        {p.lower !== undefined && p.upper !== undefined && (
          <div className="text-xs text-text-faint">
            80% interval: {p.lower.toFixed(1)}% – {p.upper.toFixed(1)}%
          </div>
        )}
      </div>
    </div>
  );
}

export default function ForecastExplorer() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const gradientId = useId().replace(/:/g, "");

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
    fetcher,
    { refreshInterval: 5 * 60 * 1000 } // models retrain hourly server-side; polling every 5min is plenty
  );

  const forecastPoints = useMemo(() => result?.forecast ?? [], [result]);
  const actualPoints = useMemo(() => result?.actual ?? [], [result]);

  const chartData = useMemo<ChartRow[]>(() => {
    const rows = new Map<string, ChartRow>();
    for (const a of actualPoints) {
      const ts = new Date(a.timestamp).getTime();
      rows.set(a.timestamp, { timestamp: a.timestamp, ts, label: formatTick(a.timestamp), actual: a.value });
    }
    for (const f of forecastPoints) {
      const existing = rows.get(f.timestamp);
      const ts = new Date(f.timestamp).getTime();
      rows.set(f.timestamp, {
        timestamp: f.timestamp,
        ts,
        label: formatTick(f.timestamp),
        actual: existing?.actual,
        predicted: f.predicted,
        lower: f.lower,
        upper: f.upper,
        band: f.upper - f.lower,
      });
    }
    return Array.from(rows.values()).sort((a, b) => a.ts - b.ts);
  }, [actualPoints, forecastPoints]);

  // "now" = where the forecast starts, used both for the vertical marker line
  // and as the anchor point for the zoom presets below.
  const nowTs = result?.generated_at ? new Date(result.generated_at).getTime() : chartData[chartData.length - 1]?.ts;

  const [zoomPreset, setZoomPreset] = useState<(typeof ZOOM_PRESETS)[number]["label"]>("All");
  const [brushKey, setBrushKey] = useState(0); // bumping this remounts <Brush>, resetting a manual drag-zoom

  const zoomRange = useMemo(() => {
    if (!chartData.length || !nowTs) return null;
    const preset = ZOOM_PRESETS.find((p) => p.label === zoomPreset);
    if (!preset || preset.hoursBack === null) return null; // "All"
    const from = nowTs - preset.hoursBack * 3600_000;
    const to = nowTs + preset.hoursForward * 3600_000;
    let startIndex = chartData.findIndex((d) => d.ts >= from);
    if (startIndex === -1) startIndex = 0;
    let endIndex = chartData.length - 1;
    for (let i = chartData.length - 1; i >= 0; i--) {
      if (chartData[i].ts <= to) {
        endIndex = i;
        break;
      }
    }
    return { startIndex, endIndex: Math.max(endIndex, startIndex + 1) };
  }, [chartData, nowTs, zoomPreset]);

  const next24h = forecastPoints.find((p) => p.horizon_hours === 24);
  const next7d = forecastPoints.find((p) => p.horizon_hours === 168);
  const lastActual = actualPoints[actualPoints.length - 1];
  const trend = lastActual && next7d ? next7d.predicted - lastActual.value : null;
  const isFallback = result?.model_type === "fallback_seasonal_persistence";

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
            <div className="mt-0.5 text-sm text-text-faint">Next 24h – 7 days, with confidence interval</div>
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
        <StatCard
          icon={CalendarClock}
          label="In 24h"
          value={next24h ? `${next24h.predicted.toFixed(1)}%` : "—"}
          color={COLOR_PREDICTED}
        />
        <StatCard
          icon={CalendarClock}
          label="In 7 days"
          value={next7d ? `${next7d.predicted.toFixed(1)}%` : "—"}
          color="var(--chart-5)"
        />
        <StatCard
          icon={TrendingUp}
          label="Trend (now → 7d)"
          value={trend !== null ? `${trend >= 0 ? "+" : ""}${trend.toFixed(1)} pts` : "—"}
          color={trend !== null && trend > 0 ? "var(--crit)" : "var(--accent)"}
        />
        <StatCard
          icon={Gauge}
          label="Model"
          value={isFallback ? "Fallback" : "ML"}
          color={isFallback ? COLOR_MODEL_FALLBACK : COLOR_MODEL_ML}
        />
      </div>

      {!isLoading && !error && result && (
        <div className="glow-insight rounded-[var(--radius-panel)] p-4">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--accent)" }}>
            <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
            Generated insight
          </div>
          <p className="mt-2 text-sm leading-relaxed text-color-text">
            {trend !== null
              ? trend > 0
                ? `${effectiveHost || "This node"}'s ${metricLabel(metric).toLowerCase()} is projected to rise by ${trend.toFixed(1)} points over the next 7 days.`
                : `${effectiveHost || "This node"}'s ${metricLabel(metric).toLowerCase()} is projected to stay flat or decline over the next 7 days.`
              : "Not enough forecast points to compute a trend yet."}
            {isFallback && (
              <>
                {" "}
                This node has limited history so far ({result.n_points_used} points) — the forecast below is
                based on its own recent pattern rather than the trained model, and the interval is wider to
                reflect that.
              </>
            )}
          </p>
        </div>
      )}

      {error && (
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          Couldn&apos;t load this forecast: {error.message}
        </div>
      )}

      {isLoading && <p className="p-2 text-sm text-text-faint">Loading forecast…</p>}

      {!isLoading && !error && !chartData.length && (
        <div className="panel p-6 text-sm text-text-faint">
          No forecast available yet for <span className="text-color-text">{effectiveHost}</span> / {metricLabel(metric)}.
        </div>
      )}

      {!!chartData.length && (
        <section className="panel p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="eyebrow">Projection</div>
              <h3 className="mt-1 flex items-center gap-1.5 text-[15px] font-semibold text-color-text">
                <MetricGlyph metric={metric} className="h-4 w-4 text-text-faint" />
                {metricLabel(metric)} forecast
              </h3>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              {result?.model_type && (
                <span className="text-xs text-text-faint">{MODEL_TYPE_LABELS[result.model_type] ?? result.model_type}</span>
              )}
              <div
                className="flex items-center gap-0.5 rounded-[var(--radius-control)] p-0.5"
                style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              >
                <ZoomIn className="ml-1.5 h-3.5 w-3.5 text-text-faint" strokeWidth={2} />
                {ZOOM_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => {
                      setZoomPreset(p.label);
                      setBrushKey((k) => k + 1); // clear any manual drag-selection
                    }}
                    className="rounded-[calc(var(--radius-control)-2px)] px-2 py-1 text-xs font-medium transition-colors"
                    style={
                      zoomPreset === p.label
                        ? { background: "var(--accent)", color: "var(--on-accent, #fff)" }
                        : { color: "var(--text-faint)" }
                    }
                  >
                    {p.label}
                  </button>
                ))}
                {zoomPreset !== "All" && (
                  <button
                    onClick={() => {
                      setZoomPreset("All");
                      setBrushKey((k) => k + 1);
                    }}
                    aria-label="Reset zoom"
                    className="ml-0.5 flex h-6 w-6 items-center justify-center rounded-[calc(var(--radius-control)-2px)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
                  >
                    <ZoomOut className="h-3.5 w-3.5" strokeWidth={2} />
                  </button>
                )}
              </div>
            </div>
          </div>

          <ResponsiveContainer width="100%" height={340}>
            <ComposedChart data={chartData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`ci-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={COLOR_PREDICTED} stopOpacity={0.22} />
                  <stop offset="100%" stopColor={COLOR_PREDICTED} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} minTickGap={40} />
              <YAxis axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} domain={[0, 100]} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--accent)", strokeWidth: 1, strokeDasharray: "4 4" }} />
              {nowTs && (
                <ReferenceLine
                  x={chartData.reduce((closest, d) => (Math.abs(d.ts - nowTs) < Math.abs(closest.ts - nowTs) ? d : closest)).label}
                  stroke="var(--text-faint)"
                  strokeDasharray="2 3"
                  label={{ value: "now", position: "insideTopRight", fill: "var(--text-faint)", fontSize: 10 }}
                />
              )}
              {/* Confidence-interval band: an invisible base area up to `lower`, then a visible
                  gradient-filled area for the `band` (upper - lower) stacked on top of it. */}
              <Area type="monotone" dataKey="lower" stackId="ci" stroke="none" fill="transparent" isAnimationActive={false} />
              <Area
                type="monotone"
                dataKey="band"
                stackId="ci"
                stroke="none"
                fill={`url(#ci-${gradientId})`}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="actual"
                stroke={COLOR_ACTUAL}
                strokeWidth={2}
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="predicted"
                stroke={COLOR_PREDICTED}
                strokeWidth={2}
                strokeDasharray="5 3"
                dot={false}
                connectNulls={false}
                isAnimationActive={false}
              />
              {/* Drag either handle below to zoom into any custom range; the preset
                  buttons above just set/reset this same brush programmatically. */}
              <Brush
                key={brushKey}
                dataKey="label"
                height={26}
                travellerWidth={8}
                startIndex={zoomRange?.startIndex}
                endIndex={zoomRange?.endIndex}
                stroke="var(--accent)"
                fill="var(--canvas)"
                tickFormatter={() => ""}
                onChange={() => setZoomPreset("All")} // manual drag no longer matches a preset button
              />
            </ComposedChart>
          </ResponsiveContainer>

          <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-text-faint">
            <span className="flex items-center gap-1.5">
              <span className="h-0.5 w-4 rounded" style={{ background: COLOR_ACTUAL }} /> Actual
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-0.5 w-4 rounded border-t-2 border-dashed" style={{ borderColor: COLOR_PREDICTED }} /> Predicted
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-4 rounded" style={{ background: COLOR_PREDICTED, opacity: 0.22 }} /> 80% interval
            </span>
            <span className="ml-auto text-text-faint/80">Drag the handles below the chart to zoom into a custom range</span>
          </div>
        </section>
      )}
    </main>
  );
}
