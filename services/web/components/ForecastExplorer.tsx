"use client";

import { useMemo, useState, useEffect, useId } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import {
  TrendingUp,
  RefreshCw,
  Sparkles,
  Cpu,
  MemoryStick,
  HardDrive,
  Activity,
  Gauge,
  CalendarClock,
  ZoomIn,
  ZoomOut,
  Repeat2,
  ListChecks,
  ArrowUpRight,
  ArrowDownRight,
  LineChart,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import type { ForecastResult, ForecastHorizonDays, DashboardNode, ThresholdWarning } from "@/lib/types";
import { FORECAST_HORIZON_DAYS } from "@/lib/types";
import { thresholdEtaLabel, metricLabel } from "@/lib/thresholds";
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
  ReferenceArea,
} from "recharts";

const FORECAST_METRICS = ["cpu_percent", "memory_percent", "disk_percent"] as const;

const MODEL_TYPE_LABELS: Record<string, string> = {
  ml_quantile: "ML model (pooled, quantile regression)",
  fallback_seasonal_persistence: "Fallback model (limited history)",
};

const HORIZON_LABELS: Record<ForecastHorizonDays, string> = {
  7: "7 days",
  30: "30 days",
  90: "90 days",
};

// Palette note: deliberately avoids --chart-3 (green) across this whole page —
// "actual" uses the blue and "predicted" uses the orange/red so the two
// series stay readable against each other without reaching for green at all.
const COLOR_ACTUAL = "var(--neutral)";
const COLOR_PREDICTED = "var(--medium)";

// Zoom-preset windows scale with the selected forecast horizon (2.8) — a 90-
// day forecast dwarfed by a "7d" zoom cap would look like a flat sliver, so
// each horizon gets its own short/medium/long/all set rather than one fixed
// list.
const ZOOM_PRESETS_BY_HORIZON: Record<ForecastHorizonDays, readonly { label: string; hoursBack: number | null; hoursForward: number | null }[]> = {
  7: [
    { label: "24h", hoursBack: 6, hoursForward: 24 },
    { label: "3d", hoursBack: 24, hoursForward: 72 },
    { label: "7d", hoursBack: 48, hoursForward: 168 },
    { label: "All", hoursBack: null, hoursForward: null },
  ],
  30: [
    { label: "7d", hoursBack: 24, hoursForward: 168 },
    { label: "14d", hoursBack: 48, hoursForward: 336 },
    { label: "30d", hoursBack: 48, hoursForward: 720 },
    { label: "All", hoursBack: null, hoursForward: null },
  ],
  90: [
    { label: "14d", hoursBack: 48, hoursForward: 336 },
    { label: "30d", hoursBack: 72, hoursForward: 720 },
    { label: "90d", hoursBack: 72, hoursForward: 2160 },
    { label: "All", hoursBack: null, hoursForward: null },
  ],
};



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

// Threshold-ETA (2.5) is treated as "no opinion" rather than an error when
// the API 404s (e.g. a metric with no default threshold) -- unlike the main
// forecast fetcher, this one resolves to null instead of throwing so a
// missing threshold doesn't put the whole page into an error state.
const thresholdFetcher = async (url: string): Promise<ThresholdWarning | null> => {
  const res = await fetch(url);
  if (!res.ok) return null;
  return res.json();
};

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
  extrapolated?: boolean;
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
        {p.extrapolated && (
          <div className="text-xs font-medium" style={{ color: "var(--warn)" }}>
            Beyond the model&apos;s trained range — wider, less certain
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

  // Selectable forecast horizon (2.8: "Extend forecast horizon to 30/90
  // days"). Falls back to the 7-day default for a missing/unrecognized URL
  // param rather than an arbitrary raw number.
  const horizonParam = Number(searchParams.get("horizon"));
  const [horizonDays, setHorizonDays] = useState<ForecastHorizonDays>(
    (FORECAST_HORIZON_DAYS as readonly number[]).includes(horizonParam) ? (horizonParam as ForecastHorizonDays) : 7
  );

  const effectiveHost = host && hostnames.includes(host) ? host : hostnames[0] ?? "";
  const effectiveNode = useMemo(() => (nodes ?? []).find((n) => n.hostname === effectiveHost), [nodes, effectiveHost]);

  useEffect(() => {
    if (!effectiveHost) return;
    const params = new URLSearchParams({ host: effectiveHost, metric, horizon: String(horizonDays) });
    router.replace(`/forecast?${params.toString()}`, { scroll: false });
  }, [effectiveHost, metric, horizonDays, router]);

  const {
    data: result,
    error,
    isLoading,
    isValidating,
    mutate,
  } = useSWR<ForecastResult>(
    effectiveHost
      ? `/api/forecast/${encodeURIComponent(effectiveHost)}/${encodeURIComponent(metric)}?horizon_days=${horizonDays}`
      : null,
    fetcher,
    { refreshInterval: 5 * 60 * 1000 } // models retrain hourly server-side; polling every 5min is plenty
  );

  const zoomPresets = ZOOM_PRESETS_BY_HORIZON[horizonDays];

  const forecastPoints = useMemo(() => result?.forecast ?? [], [result]);
  const actualPoints = useMemo(() => result?.actual ?? [], [result]);

  // Threshold-breach ETA (2.5) for whichever node/metric is currently
  // selected -- same data the dashboard's warnings panel is built from, just
  // scoped to one resource and rendered alongside its chart here. Searches
  // out to the same horizon selected for the chart (2.8) rather than always
  // being capped at 7 days.
  const { data: thresholdWarning } = useSWR<ThresholdWarning | null>(
    effectiveHost
      ? `/api/forecast/${encodeURIComponent(effectiveHost)}/${encodeURIComponent(metric)}/threshold?horizon_days=${horizonDays}`
      : null,
    thresholdFetcher,
    { refreshInterval: 5 * 60 * 1000 }
  );

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
        extrapolated: f.extrapolated,
      });
    }
    return Array.from(rows.values()).sort((a, b) => a.ts - b.ts);
  }, [actualPoints, forecastPoints]);

  // "now" = where the forecast starts, used both for the vertical marker line
  // and as the anchor point for the zoom presets below.
  const nowTs = result?.generated_at ? new Date(result.generated_at).getTime() : chartData[chartData.length - 1]?.ts;

  const [zoomPreset, setZoomPreset] = useState<string>("All");
  const [brushKey, setBrushKey] = useState(0); // bumping this remounts <Brush>, resetting a manual drag-zoom

  // The preset label set differs per horizon (e.g. "3d" doesn't exist in the
  // 90-day set) -- reset to "All" whenever the horizon selection changes so
  // a stale label from the previous set never lingers.
  useEffect(() => {
    setZoomPreset("All");
    setBrushKey((k) => k + 1);
  }, [horizonDays]);

  const zoomRange = useMemo(() => {
    if (!chartData.length || !nowTs) return null;
    const preset = zoomPresets.find((p) => p.label === zoomPreset);
    if (!preset || preset.hoursBack === null || preset.hoursForward === null) return null; // "All"
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
  }, [chartData, nowTs, zoomPreset, zoomPresets]);

  const next24h = forecastPoints.find((p) => p.horizon_hours === 24);
  const next48h = forecastPoints.find((p) => p.horizon_hours === 48);
  const next72h = forecastPoints.find((p) => p.horizon_hours === 72);
  const next7d = forecastPoints.find((p) => p.horizon_hours === 168);
  const next30d = forecastPoints.find((p) => p.horizon_hours === 720);
  const next90d = forecastPoints.find((p) => p.horizon_hours === 2160);
  const horizonRows = [
    { label: "In 24 hours", point: next24h },
    { label: "In 2 days", point: next48h },
    { label: "In 3 days", point: next72h },
    { label: "In 7 days", point: next7d },
    ...(next30d ? [{ label: "In 30 days", point: next30d }] : []),
    ...(next90d ? [{ label: "In 90 days", point: next90d }] : []),
  ];
  // First point the API flagged as extrapolated (2.8) -- past the ML
  // model's training-supported range -- used to shade that part of the
  // chart and to drive the "extrapolated beyond ~Nd" note below it.
  const firstExtrapolated = forecastPoints.find((p) => p.extrapolated);
  const lastActual = actualPoints[actualPoints.length - 1];
  const trend = lastActual && next7d ? next7d.predicted - lastActual.value : null;
  const trend24h = lastActual && next24h ? next24h.predicted - lastActual.value : null;
  const isFallback = result?.model_type === "fallback_seasonal_persistence";

  return (
    <main className="grid gap-4">
      {/* Slim top strip — brand + refresh only; node/metric selection lives in
          the sidebar "forecast for" widget below, closer to where it's used. */}
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-3">
          <div
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
            style={{
              background: "linear-gradient(155deg, var(--accent), var(--medium))",
              boxShadow: "0 2px 10px -3px color-mix(in srgb, var(--medium) 45%, transparent)",
            }}
          >
            <TrendingUp className="h-4.5 w-4.5 text-white" strokeWidth={1.75} />
          </div>
          <div>
            <div className="eyebrow">Monitoring</div>
            <h1 className="font-display mt-0.5 text-lg font-semibold text-color-text">Usage Forecast</h1>
          </div>
        </div>

        <button
          onClick={() => mutate()}
          aria-label="Refresh forecast"
          className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
          style={{ border: "1px solid var(--border)" }}
        >
          <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
        </button>
      </div>

      {/* Two-column widget layout: chart + supporting detail widgets on the
          left, a stack of compact widgets (forecast selector, summary
          number, AI tips) on the right — mirrors a "dashboard with a hero
          chart + widget rail" layout instead of a stat-row-then-chart stack. */}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
        {/* ---- LEFT: hero chart + detail widgets ---- */}
        <div className="grid gap-4">
          {error && (
            <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
              Couldn&apos;t load this forecast: {error.message}
            </div>
          )}

          {isLoading && (
            <div className="panel p-6 text-sm text-text-faint">Loading forecast…</div>
          )}

          {!isLoading && !error && !chartData.length && (
            <div className="panel p-6 text-sm text-text-faint">
              No forecast available yet for <span className="text-color-text">{effectiveHost}</span> / {metricLabel(metric)}.
            </div>
          )}

          {!!chartData.length && (
            <>
              <section className="panel p-5">
                <div className="mb-1 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="eyebrow flex items-center gap-1.5">
                      <MetricGlyph metric={metric} className="h-3.5 w-3.5" />
                      {effectiveHost || "Node"} · {metricLabel(metric)}
                    </div>
                    <div className="mt-1 flex items-baseline gap-2">
                      <span className="stat-figure text-[26px] text-color-text">
                        {lastActual ? `${lastActual.value.toFixed(1)}%` : "—"}
                      </span>
                      {trend24h !== null && (
                        <span
                          className="inline-flex items-center gap-0.5 text-xs font-semibold"
                          style={{ color: trend24h > 0 ? "var(--accent)" : "var(--text-faint)" }}
                        >
                          {trend24h > 0 ? (
                            <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2.5} />
                          ) : (
                            <ArrowDownRight className="h-3.5 w-3.5" strokeWidth={2.5} />
                          )}
                          {trend24h >= 0 ? "+" : ""}
                          {trend24h.toFixed(1)} pts in 24h
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    {result?.model_type && (
                      <span
                        className="rounded-[var(--radius-control)] border px-2 py-1 text-xs font-medium"
                        style={{
                          borderColor: isFallback ? "var(--warn-soft)" : "var(--border)",
                          background: isFallback ? "var(--warn-soft)" : "var(--canvas)",
                          color: isFallback ? "var(--warn)" : "var(--text-dim)",
                        }}
                      >
                        {MODEL_TYPE_LABELS[result.model_type] ?? result.model_type}
                      </span>
                    )}
                    <div
                      className="flex items-center gap-0.5 rounded-[var(--radius-control)] p-0.5"
                      style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
                    >
                      <ZoomIn className="ml-1.5 h-3.5 w-3.5 text-text-faint" strokeWidth={2} />
                      {zoomPresets.map((p) => (
                        <button
                          key={p.label}
                          onClick={() => {
                            setZoomPreset(p.label);
                            setBrushKey((k) => k + 1); // clear any manual drag-selection
                          }}
                          className="rounded-[calc(var(--radius-control)-2px)] px-2 py-1 text-xs font-medium transition-colors"
                          style={
                            zoomPreset === p.label
                              ? { background: "var(--medium)", color: "#fff" }
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

                <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={chartData} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id={`ci-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={COLOR_PREDICTED} stopOpacity={0.4} />
                        <stop offset="100%" stopColor={COLOR_PREDICTED} stopOpacity={0.08} />
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
                    {thresholdWarning && (
                      <ReferenceLine
                        y={thresholdWarning.threshold}
                        stroke="var(--crit)"
                        strokeDasharray="4 3"
                        strokeOpacity={0.6}
                        label={{
                          value: `${thresholdWarning.threshold}% threshold`,
                          position: "insideBottomLeft",
                          fill: "var(--crit)",
                          fontSize: 10,
                        }}
                      />
                    )}
                    {firstExtrapolated && chartData.length > 0 && (
                      <ReferenceArea
                        x1={formatTick(firstExtrapolated.timestamp)}
                        x2={chartData[chartData.length - 1].label}
                        stroke="none"
                        fill="var(--warn)"
                        fillOpacity={0.05}
                        label={{
                          value: "extrapolated",
                          position: "insideTopLeft",
                          fill: "var(--warn)",
                          fontSize: 10,
                        }}
                      />
                    )}
                    {/* Confidence-interval band: an invisible base area up to `lower`, then a visible
                        gradient-filled area for the `band` (upper - lower) stacked on top of it. */}
                    <Area type="monotone" dataKey="lower" stackId="ci" stroke="none" fill="transparent" isAnimationActive={false} />
                    <Area
                      type="monotone"
                      dataKey="band"
                      stackId="ci"
                      stroke={COLOR_PREDICTED}
                      strokeOpacity={0.35}
                      strokeWidth={1}
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
                    <span className="h-2.5 w-4 rounded" style={{ background: COLOR_PREDICTED, opacity: 0.4 }} /> 80% interval
                  </span>
                  {firstExtrapolated && (
                    <span className="flex items-center gap-1.5">
                      <span className="h-2.5 w-4 rounded" style={{ background: "var(--warn)", opacity: 0.15 }} /> Extrapolated (beyond trained range)
                    </span>
                  )}
                  <span className="ml-auto text-text-faint/80">Drag the handles below the chart to zoom into a custom range</span>
                </div>
              </section>

              {/* Two detail widgets below the chart, side by side — the
                  "forecast horizons" list and "model details" checklist,
                  echoing a reference dashboard's paired list-widgets row. */}
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="panel p-4">
                  <div className="eyebrow flex items-center gap-1.5">
                    <CalendarClock className="h-3.5 w-3.5" />
                    Forecast at a glance
                  </div>
                  <div className="mt-3 grid gap-1">
                    {horizonRows.map((row) => {
                      const delta = lastActual && row.point ? row.point.predicted - lastActual.value : null;
                      return (
                        <div key={row.label} className="flex items-center justify-between rounded-[var(--radius-control)] px-2 py-2 transition-colors hover:bg-[var(--canvas)]">
                          <span className="text-sm text-text-dim">{row.label}</span>
                          <span className="flex items-center gap-2">
                            <span className="stat-figure text-sm text-color-text">
                              {row.point ? `${row.point.predicted.toFixed(1)}%` : "—"}
                            </span>
                            {delta !== null && (
                              <span
                                className="inline-flex items-center gap-0.5 text-[11px] font-medium"
                                style={{ color: delta > 0 ? "var(--accent)" : "var(--text-faint)" }}
                              >
                                {delta > 0 ? (
                                  <ArrowUpRight className="h-3 w-3" strokeWidth={2.5} />
                                ) : (
                                  <ArrowDownRight className="h-3 w-3" strokeWidth={2.5} />
                                )}
                                {delta >= 0 ? "+" : ""}
                                {delta.toFixed(1)}
                              </span>
                            )}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div className="panel p-4">
                  <div className="eyebrow flex items-center gap-1.5">
                    <ListChecks className="h-3.5 w-3.5" />
                    Model details
                  </div>
                  <div className="mt-3 grid gap-1">
                    {[
                      { label: "Node", value: effectiveHost || "—" },
                      { label: "Metric", value: metricLabel(metric) },
                      { label: "Model", value: isFallback ? "Fallback (seasonal persistence)" : "ML (pooled, quantile)" },
                      { label: "Horizon requested", value: result ? `${result.horizon_days} days` : "—" },
                      {
                        label: "Trusted ML range",
                        value: firstExtrapolated
                          ? `First ${Math.round((firstExtrapolated.horizon_hours - 1) / 24)}d — beyond that is extrapolated`
                          : result
                            ? "Full requested horizon"
                            : "—",
                      },
                      { label: "Training points used", value: result ? String(result.n_points_used) : "—" },
                      {
                        label: "Generated",
                        value: result?.generated_at ? new Date(result.generated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—",
                      },
                    ].map((row) => (
                      <div key={row.label} className="flex items-center justify-between rounded-[var(--radius-control)] px-2 py-2 transition-colors hover:bg-[var(--canvas)]">
                        <span className="text-sm text-text-dim">{row.label}</span>
                        <span className="max-w-[55%] truncate text-right text-sm text-color-text">{row.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* ---- RIGHT: widget rail ---- */}
        <div className="grid gap-4 content-start">
          {/* "Forecast for" widget — node/metric selection, styled like a
              compact control card rather than plain top-bar dropdowns. */}
          <div className="panel p-4">
            <div className="eyebrow flex items-center gap-1.5">
              <Repeat2 className="h-3.5 w-3.5" />
              Forecast for
            </div>
            <div className="mt-3 grid gap-2">
              <label className="grid gap-1">
                <span className="text-xs text-text-faint">Node</span>
                <select value={effectiveHost} onChange={(e) => setHost(e.target.value)} className={selectClass} style={selectStyle}>
                  {!hostnames.length && <option value="">No nodes</option>}
                  {hostnames.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1">
                <span className="text-xs text-text-faint">Metric</span>
                <select value={metric} onChange={(e) => setMetric(e.target.value)} className={selectClass} style={selectStyle}>
                  {FORECAST_METRICS.map((m) => (
                    <option key={m} value={m}>
                      {metricLabel(m)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid gap-1">
                <span className="text-xs text-text-faint">Horizon</span>
                <div
                  className="flex items-center gap-0.5 rounded-[var(--radius-control)] p-0.5"
                  style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
                >
                  {FORECAST_HORIZON_DAYS.map((days) => (
                    <button
                      key={days}
                      onClick={() => setHorizonDays(days)}
                      className="flex-1 rounded-[calc(var(--radius-control)-2px)] py-1.5 text-xs font-medium transition-colors"
                      style={
                        horizonDays === days
                          ? { background: "var(--medium)", color: "#fff" }
                          : { color: "var(--text-faint)" }
                      }
                    >
                      {days}d
                    </button>
                  ))}
                </div>
              </div>
              <button
                onClick={() => mutate()}
                className="mt-1 inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-control)] py-2 text-sm font-medium text-text-dim transition-colors hover:bg-[var(--canvas)]"
                style={{ border: "1px solid var(--border)", background: "var(--surface)" }}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
                Refresh forecast
              </button>
            </div>
          </div>

          {/* Summary widget — the "balance card" analog: headline predicted
              numbers with trend badges. */}
          {!isLoading && !error && result && (
            <div
              className="panel border-l-[3px] p-4"
              style={{
                borderLeftColor: "var(--medium)",
                boxShadow: "var(--shadow), 0 8px 20px -14px color-mix(in srgb, var(--medium) 60%, transparent)",
              }}
            >
              <div className="eyebrow flex items-center gap-1.5">
                <Gauge className="h-3.5 w-3.5" />
                Predicted {metricLabel(metric).toLowerCase()}
              </div>
              <div className="mt-2 flex items-center justify-between">
                <div>
                  <div className="text-[11px] text-text-faint">In 24h</div>
                  <div className="stat-figure text-xl text-color-text">{next24h ? `${next24h.predicted.toFixed(1)}%` : "—"}</div>
                </div>
                <div className="h-8 w-px" style={{ background: "var(--border)" }} />
                <div className="text-right">
                  <div className="text-[11px] text-text-faint">In 7 days</div>
                  <div className="stat-figure text-xl text-color-text">{next7d ? `${next7d.predicted.toFixed(1)}%` : "—"}</div>
                </div>
              </div>
              {trend !== null && (
                <div
                  className="mt-3 inline-flex items-center gap-1 rounded-[var(--radius-control)] px-2 py-1 text-xs font-semibold"
                  style={{
                    background: trend > 0 ? "var(--accent-soft)" : "var(--neutral-soft)",
                    color: trend > 0 ? "var(--accent)" : "var(--text-faint)",
                  }}
                >
                  {trend > 0 ? <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2.5} /> : <ArrowDownRight className="h-3.5 w-3.5" strokeWidth={2.5} />}
                  {trend >= 0 ? "+" : ""}
                  {trend.toFixed(1)} pts over 7d
                </div>
              )}
            </div>
          )}

          {/* Threshold-breach ETA widget (2.5) -- "X will hit threshold in
              ~N days" for the currently selected node/metric, or a calm
              all-clear when nothing's projected to cross within 7 days. */}
          {!isLoading && !error && result && thresholdWarning && (
            <div
              className="panel border-l-[3px] p-4"
              style={{
                borderLeftColor: thresholdWarning.will_breach
                  ? thresholdWarning.already_breached
                    ? "var(--crit)"
                    : "var(--warn)"
                  : "var(--ok)",
              }}
            >
              <div className="eyebrow flex items-center gap-1.5">
                {thresholdWarning.will_breach ? (
                  <ShieldAlert className="h-3.5 w-3.5" style={{ color: thresholdWarning.already_breached ? "var(--crit)" : "var(--warn)" }} />
                ) : (
                  <ShieldCheck className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} />
                )}
                Threshold ({thresholdWarning.threshold}%)
              </div>
              {thresholdWarning.will_breach ? (
                <>
                  <div className="mt-2 text-sm text-color-text">
                    <span className="font-medium">{effectiveHost}</span> will hit {thresholdWarning.threshold}%{" "}
                    <span
                      className="font-semibold"
                      style={{ color: thresholdWarning.already_breached ? "var(--crit)" : "var(--warn)" }}
                    >
                      {thresholdEtaLabel(thresholdWarning)}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-text-faint">now at {thresholdWarning.current_value}%</div>
                </>
              ) : (
                <div className="mt-2 text-sm text-text-faint">
                  Not projected to hit {thresholdWarning.threshold}% within the next {horizonDays} days (now at {thresholdWarning.current_value}%).
                </div>
              )}
            </div>
          )}

          {/* AI tips widget — generated-insight copy, restyled as a bulleted
              "tips" card to match the widget-rail language. */}
          {!isLoading && !error && result && (
            <div
              className="panel border-l-[3px] p-4"
              style={{
                borderLeftColor: "var(--medium)",
                boxShadow: "var(--shadow), 0 8px 20px -14px color-mix(in srgb, var(--medium) 60%, transparent)",
              }}
            >
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--medium)" }}>
                <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
                AI tips
              </div>
              <ul className="mt-2.5 grid gap-2 text-sm leading-relaxed text-color-text">
                <li className="flex gap-2">
                  <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full" style={{ background: "var(--text-muted)" }} />
                  <span>
                    {trend !== null
                      ? trend > 0
                        ? `${effectiveHost || "This node"}'s ${metricLabel(metric).toLowerCase()} is projected to rise by ${trend.toFixed(1)} points over the next 7 days.`
                        : `${effectiveHost || "This node"}'s ${metricLabel(metric).toLowerCase()} is projected to stay flat or decline over the next 7 days.`
                      : "Not enough forecast points to compute a trend yet."}
                  </span>
                </li>
                {isFallback && (
                  <li className="flex gap-2">
                    <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full" style={{ background: "var(--text-muted)" }} />
                    <span>
                      This node has limited history so far ({result.n_points_used} points) — the forecast is based
                      on its own recent pattern rather than the trained model, and the interval is wider to reflect
                      that.
                    </span>
                  </li>
                )}
                {!isFallback && firstExtrapolated && (
                  <li className="flex gap-2">
                    <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full" style={{ background: "var(--text-muted)" }} />
                    <span>
                      Past {Math.round((firstExtrapolated.horizon_hours - 1) / 24)} days out, there isn&apos;t enough
                      retained history yet for the trained model to have learned that far — the shaded part of the
                      chart widens to reflect the extra uncertainty instead of guessing confidently.
                    </span>
                  </li>
                )}
              </ul>
            </div>
          )}

          {/* CTA widget — clean bordered callout instead of a full-bleed
              gradient block, pointing at this node's live metrics page. */}
          {effectiveNode && (
            <Link
              href={`/nodes/${encodeURIComponent(effectiveNode.instance)}`}
              className="group panel flex items-start gap-3 p-4 transition-colors hover:border-[var(--medium)]"
            >
              <div
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
                style={{
                  background: "linear-gradient(155deg, var(--accent), var(--medium))",
                  boxShadow: "0 2px 8px -3px color-mix(in srgb, var(--medium) 50%, transparent)",
                }}
              >
                <LineChart className="h-4 w-4 text-white" strokeWidth={2} />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium text-color-text">Explore live metrics</div>
                <div className="mt-0.5 text-xs text-text-faint">See {effectiveHost}&apos;s full metrics history</div>
                <div
                  className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium transition-transform group-hover:translate-x-0.5"
                  style={{ color: "var(--medium)" }}
                >
                  Open metrics
                  <ArrowUpRight className="h-3 w-3" strokeWidth={2.5} />
                </div>
              </div>
            </Link>
          )}
        </div>
      </div>
    </main>
  );
}
