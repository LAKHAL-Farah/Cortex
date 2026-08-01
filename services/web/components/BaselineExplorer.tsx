"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  TrendingUp,
  RefreshCw,
  Sparkles,
  Server,
  Cpu,
  MemoryStick,
  Activity,
  Layers,
  CalendarClock,
} from "lucide-react";
import type { BaselineSlot, DashboardNode } from "@/lib/types";
import { metricLabel, formatRelative } from "@/lib/anomalies";
import {
  BASELINE_METRICS,
  HOURS,
  WEEKDAY_LABELS,
  WEEKDAY_LABELS_LONG,
  buildBaselineInsight,
  cellIntensity,
  coverage,
  hourLabel,
  indexSlots,
  jsDayToWeekday,
  mostRecentUpdate,
  totalSamples,
} from "@/lib/baselines";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data as BaselineSlot[];
};

const nodesFetcher = (url: string) => fetch(url).then((r) => r.json());

const selectClass = "rounded-[var(--radius-control)] px-3 py-2 text-sm text-color-text outline-none transition-colors";
const selectStyle = { border: "1px solid var(--border)", background: "var(--canvas)" } as const;

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

function StatCard({
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: typeof Layers;
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

type CurvePoint = { hour: number; label: string; median: number | null; mad: number | null; low: number | null; band: number | null; sample_count: number };

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ payload: CurvePoint }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  return (
    <div className="panel p-3.5 text-sm text-color-text" style={{ boxShadow: "var(--shadow-hover)" }}>
      <div className="eyebrow">{label}</div>
      <div className="mt-2 flex items-center gap-2.5">
        <span className="status-dot" style={{ background: "var(--chart-1)" }} />
        <span className="font-medium">Median</span>{" "}
        <span className="stat-figure text-text-faint">{p.median?.toFixed(1)}%</span>
      </div>
      <div className="mt-1 flex items-center gap-2.5">
        <span className="status-dot" style={{ background: "var(--chart-4)" }} />
        <span className="font-medium">MAD</span>{" "}
        <span className="stat-figure text-text-faint">±{p.mad?.toFixed(1)}</span>
      </div>
      <div className="mt-1 text-xs text-text-faint">{p.sample_count ?? 0} samples backing this hour</div>
    </div>
  );
}

export default function BaselineExplorer() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const { data: nodes } = useSWR<DashboardNode[]>("/api/dashboard", nodesFetcher, { refreshInterval: 15000 });
  const hostnames = useMemo(() => (nodes ?? []).map((n) => n.hostname).sort(), [nodes]);

  const [host, setHost] = useState<string>(searchParams.get("host") ?? "");
  const [metric, setMetric] = useState<string>(searchParams.get("metric") ?? BASELINE_METRICS[0]);
  const [weekday, setWeekday] = useState<number>(jsDayToWeekday(new Date().getDay()));

  // Derived rather than synced via setState-in-effect: falls back to the
  // first available node once the list loads (or if a stale/unknown
  // hostname was in the URL), without fighting an explicit user selection.
  const effectiveHost = host && hostnames.includes(host) ? host : hostnames[0] ?? "";

  useEffect(() => {
    if (!effectiveHost) return;
    const params = new URLSearchParams({ host: effectiveHost, metric });
    router.replace(`/baselines?${params.toString()}`, { scroll: false });
  }, [effectiveHost, metric, router]);

  const {
    data: slots,
    error,
    isLoading,
    isValidating,
    mutate,
  } = useSWR<BaselineSlot[]>(
    effectiveHost ? `/api/baselines/${encodeURIComponent(effectiveHost)}?metric_name=${encodeURIComponent(metric)}` : null,
    fetcher
  );

  const slotList = useMemo(() => slots ?? [], [slots]);
  const byKey = useMemo(() => indexSlots(slotList), [slotList]);
  const cov = coverage(slotList);
  const samples = totalSamples(slotList);
  const lastUpdate = mostRecentUpdate(slotList);
  const peakMedian = slotList.length ? Math.max(...slotList.map((s) => s.median)) : 0;
  const insight = buildBaselineInsight(effectiveHost || "This node", metricLabel(metric), slotList);

  const [selected, setSelected] = useState<{ weekday: number; hour: number } | null>(null);
  const selectedSlot = selected ? byKey.get(`${selected.weekday}-${selected.hour}`) ?? null : null;

  const curveData = HOURS.map((hour) => {
    const slot = byKey.get(`${weekday}-${hour}`);
    return {
      hour,
      label: hourLabel(hour),
      median: slot?.median ?? null,
      mad: slot?.mad ?? null,
      low: slot ? Math.max(slot.median - slot.mad, 0) : null,
      band: slot ? slot.mad * 2 : null,
      sample_count: slot?.sample_count ?? 0,
    };
  });

  return (
    <main className="grid gap-4">
      <div className="glow-surface panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <TrendingUp className="h-4.5 w-4.5 text-text-dim" strokeWidth={1.75} />
          </div>
          <div>
            <div className="eyebrow">Monitoring</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Pattern Baselines</h1>
            <div className="mt-0.5 text-sm text-text-faint">Learned normal-usage curves per node &amp; metric</div>
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
            {BASELINE_METRICS.map((m) => (
              <option key={m} value={m}>
                {metricLabel(m)}
              </option>
            ))}
          </select>
          <button
            onClick={() => mutate()}
            aria-label="Refresh baseline"
            className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)" }}
          >
            <RefreshCw className={`h-4 w-4 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard icon={Layers} label="Coverage of weekly cycle" value={`${cov}%`} color="var(--accent)" />
        <StatCard icon={Server} label="Samples backing model" value={samples.toLocaleString()} color="var(--chart-3)" />
        <StatCard icon={TrendingUp} label="Peak median" value={slotList.length ? `${peakMedian.toFixed(1)}%` : "—"} color="var(--chart-1)" />
        <StatCard icon={CalendarClock} label="Last updated" value={lastUpdate ? formatRelative(lastUpdate) : "—"} color="var(--chart-4)" />
      </div>

      {!isLoading && !error && (
        <div className="glow-insight rounded-[var(--radius-panel)] p-4">
          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--accent)" }}>
            <Sparkles className="h-3.5 w-3.5" strokeWidth={2} />
            Generated insight
          </div>
          <p className="mt-2 text-sm leading-relaxed text-color-text">{insight}</p>
        </div>
      )}

      {error && (
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          Couldn&apos;t load this baseline: {error.message}
        </div>
      )}

      {isLoading && <p className="p-2 text-sm text-text-faint">Loading baseline…</p>}

      {!isLoading && !error && !slotList.length && (
        <div className="panel p-6 text-sm text-text-faint">
          No baseline computed yet for <span className="text-color-text">{effectiveHost}</span> / {metricLabel(metric)}. The builder
          populates this hourly once enough history has accumulated for this node — check back later.
        </div>
      )}

      {!!slotList.length && (
        <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
          <section className="panel p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="eyebrow">Weekly heatmap</div>
                <h3 className="mt-1 flex items-center gap-1.5 text-[15px] font-semibold text-color-text">
                  <MetricGlyph metric={metric} className="h-4 w-4 text-text-faint" />
                  {metricLabel(metric)} by weekday &amp; hour
                </h3>
              </div>
            </div>

            <div className="overflow-x-auto">
              <div className="min-w-[640px]">
                <div className="grid" style={{ gridTemplateColumns: "40px repeat(24, minmax(0,1fr))" }}>
                  <div />
                  {HOURS.map((h) => (
                    <div key={h} className="pb-1 text-center text-[9px] text-text-muted">
                      {h % 3 === 0 ? hourLabel(h) : ""}
                    </div>
                  ))}
                </div>
                {WEEKDAY_LABELS.map((label, w) => (
                  <div key={label} className="grid items-center gap-y-1" style={{ gridTemplateColumns: "40px repeat(24, minmax(0,1fr))" }}>
                    <div className="text-xs text-text-faint">{label}</div>
                    {HOURS.map((h) => {
                      const slot = byKey.get(`${w}-${h}`);
                      const isSelected = selected?.weekday === w && selected?.hour === h;
                      const intensity = slot ? cellIntensity(slot.median) : 0;
                      return (
                        <button
                          key={h}
                          onClick={() => setSelected({ weekday: w, hour: h })}
                          title={
                            slot
                              ? `${WEEKDAY_LABELS_LONG[w]} ${hourLabel(h)} — median ${slot.median.toFixed(1)}%, ${slot.sample_count} samples`
                              : `${WEEKDAY_LABELS_LONG[w]} ${hourLabel(h)} — no data yet`
                          }
                          className="m-[1.5px] aspect-square rounded-[3px] transition-transform hover:scale-110"
                          style={{
                            background: slot
                              ? `color-mix(in srgb, var(--chart-1) ${Math.round(intensity * 90) + 10}%, var(--canvas))`
                              : "var(--border-soft)",
                            outline: isSelected ? "2px solid var(--accent)" : "none",
                            outlineOffset: 1,
                            opacity: slot ? 1 : 0.5,
                          }}
                        />
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-3 flex items-center gap-2 text-xs text-text-faint">
              <span>Low</span>
              <div
                className="h-2 w-24 rounded-full"
                style={{ background: "linear-gradient(90deg, color-mix(in srgb, var(--chart-1) 10%, var(--canvas)), var(--chart-1))" }}
              />
              <span>High</span>
              <span className="ml-auto">Click a cell for details</span>
            </div>

            {selectedSlot && (
              <div className="mt-4 panel divide-y p-1" style={{ borderColor: "var(--border-soft)" }}>
                {[
                  { label: "Slot", value: `${WEEKDAY_LABELS_LONG[selectedSlot.weekday]} · ${hourLabel(selectedSlot.hour)}` },
                  { label: "Median (robust)", value: `${selectedSlot.median.toFixed(2)}%` },
                  { label: "MAD", value: `±${selectedSlot.mad.toFixed(2)}` },
                  { label: "Mean", value: `${selectedSlot.mean.toFixed(2)}%` },
                  { label: "Std dev", value: `±${selectedSlot.stddev.toFixed(2)}` },
                  { label: "Sample count", value: selectedSlot.sample_count },
                  { label: "Updated", value: selectedSlot.updated_at ? formatRelative(selectedSlot.updated_at) : "—" },
                ].map((row) => (
                  <div key={row.label} className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
                    <span className="text-text-faint">{row.label}</span>
                    <span className="stat-figure text-color-text">{row.value}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="eyebrow">Daily curve</div>
                <h3 className="mt-1 text-[15px] font-semibold text-color-text">Expected pattern for {WEEKDAY_LABELS_LONG[weekday]}</h3>
              </div>
              <div className="inline-flex flex-wrap rounded-[var(--radius-control)] p-0.5" style={{ border: "1px solid var(--border)" }}>
                {WEEKDAY_LABELS.map((label, w) => (
                  <button
                    key={label}
                    onClick={() => setWeekday(w)}
                    className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
                    style={{
                      background: weekday === w ? "var(--accent)" : "transparent",
                      color: weekday === w ? "#fff" : "var(--text-dim)",
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={curveData} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} />
                <YAxis axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 11 }} domain={[0, 100]} />
                <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--accent)", strokeWidth: 1, strokeDasharray: "4 4" }} />

                <defs>
                  <linearGradient id="baselineBand" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor="var(--chart-4)" stopOpacity={0.22} />
                    <stop offset="100%" stopColor="var(--chart-4)" stopOpacity={0.05} />
                  </linearGradient>
                </defs>

                {/* invisible base + colored band stacked on top gives a
                    (median - mad) .. (median + mad) shaded range */}
                <Area type="monotone" dataKey="low" stackId="band" stroke="none" fill="transparent" isAnimationActive={false} />
                <Area type="monotone" dataKey="band" stackId="band" stroke="none" fill="url(#baselineBand)" isAnimationActive={false} />

                <Line
                  type="monotone"
                  dataKey="median"
                  stroke="var(--chart-1)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 5, stroke: "var(--chart-1)", strokeWidth: 2, fill: "#fff" }}
                  connectNulls
                />
              </ComposedChart>
            </ResponsiveContainer>

            <div className="mt-3 flex items-center gap-4 text-xs text-text-faint">
              <span className="inline-flex items-center gap-1.5">
                <span className="status-dot" style={{ background: "var(--chart-1)" }} />
                Median
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-3 rounded-sm" style={{ background: "var(--chart-4)", opacity: 0.35 }} />
                ± MAD band
              </span>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
