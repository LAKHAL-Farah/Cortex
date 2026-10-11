"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  SlidersHorizontal,
  RotateCcw,
  Cpu,
  MemoryStick,
  HardDrive,
  Gauge,
  ServerCog,
  CalendarClock,
  TrendingUp,
  Info,
  Loader2,
  TriangleAlert,
  CircleCheck,
  Wallet,
} from "lucide-react";
import {
  ComposedChart,
  Area,
  Line,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  ReferenceLine,
  ReferenceArea,
} from "recharts";
import type {
  CapacityMetric,
  CapacityOptions,
  CapacityParams,
  CapacityReservation,
  CapacityResource,
  CapacityResult,
  ReservationRow,
} from "@/lib/capacity";
import { VERDICT_STYLE, breachLabel, formatDays } from "@/lib/capacity";

type AvailableResource = Extract<CapacityResource, { available: true }>;

// Same convention as the Forecast page: neutral for what happened, the
// accent for what the scenario predicts. Baseline is the dashed neutral so
// the scenario's divergence from it is the thing the eye lands on.
const COLOR_ACTUAL = "var(--neutral)";
const COLOR_BASELINE = "var(--medium)";
const COLOR_SCENARIO = "var(--accent)";

const HOUR_MS = 3600_000;

const selectStyle = { border: "1px solid var(--border)", background: "var(--canvas)" } as const;

function MetricGlyph({ metric, className }: { metric: CapacityMetric; className?: string }) {
  const Icon = metric === "cpu_percent" ? Cpu : metric === "memory_percent" ? MemoryStick : HardDrive;
  return <Icon className={className} strokeWidth={1.75} />;
}

function formatTick(ms: number): string {
  return new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric" });
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const optionsFetcher = async (url: string): Promise<CapacityOptions> => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok || !data) throw new Error((data && (data.detail || data.message)) || `Request failed (${res.status})`);
  return data;
};

/** FastAPI's 422 `detail` is a list of {loc, msg}; everything else is a string. */
function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? "invalid"}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}

/** Re-runs the simulation shortly after the last parameter change. The
 * previous result stays on screen while the next one loads, so dragging a
 * slider updates the chart in place instead of flashing a spinner. */
function useSimulation(params: CapacityParams | null) {
  const [data, setData] = useState<CapacityResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const key = params ? JSON.stringify(params) : null;

  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setPending(true);
      try {
        const res = await fetch("/api/capacity/simulate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: key,
          signal: controller.signal,
        });
        const body = await res.json().catch(() => null);
        if (!res.ok) throw new Error(errorMessage(body, res.status));
        setData(body as CapacityResult);
        setError(null);
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError((e as Error).message);
      } finally {
        if (!controller.signal.aborted) setPending(false);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key]);

  return { data, error, pending };
}

// ------------------------------------------------------------- controls --
function Param({
  label,
  unit,
  value,
  onChange,
  min,
  max,
  sliderMax,
  step = 1,
  isDefault,
  hint,
}: {
  label: string;
  unit?: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  sliderMax?: number;
  step?: number;
  isDefault: boolean;
  hint?: string;
}) {
  const clamp = (v: number) => Math.min(Math.max(Number.isFinite(v) ? v : min, min), max);
  const sMax = Math.min(sliderMax ?? max, max);
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-1.5 text-xs text-text-dim">
          {!isDefault && <span className="status-dot" style={{ background: COLOR_SCENARIO }} title="Changed from default" />}
          {label}
        </label>
        <span className="flex items-center gap-1">
          <input
            type="number"
            value={Number.isFinite(value) ? value : ""}
            min={min}
            max={max}
            step={step}
            onChange={(e) => onChange(clamp(Number(e.target.value)))}
            className="stat-figure w-[4.5rem] rounded-[var(--radius-control)] px-2 py-1 text-right text-sm text-color-text outline-none"
            style={selectStyle}
            aria-label={label}
          />
          {unit && <span className="w-9 text-[11px] text-text-faint">{unit}</span>}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={sMax}
        step={step}
        value={Math.min(value, sMax)}
        onChange={(e) => onChange(clamp(Number(e.target.value)))}
        className="w-full"
        style={{ accentColor: "var(--accent)" }}
        aria-label={`${label} slider`}
      />
      {hint && <p className="text-[11px] leading-snug text-text-faint">{hint}</p>}
    </div>
  );
}

function Group({ icon: Icon, title, children }: { icon: typeof Cpu; title: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-3">
      <div className="eyebrow flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5" />
        {title}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- chart --
type ChartRow = {
  ts: number;
  actual?: number;
  baseline?: number;
  scenario?: number;
  lower?: number;
  band?: number;
  bandLower?: number;
  bandUpper?: number;
};

function ChartTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ChartRow }> }) {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload;
  if (!p) return null;
  const delta = p.scenario !== undefined && p.baseline !== undefined ? p.scenario - p.baseline : null;
  return (
    <div className="panel p-3.5 text-sm text-color-text" style={{ boxShadow: "var(--shadow-hover)" }}>
      <div className="eyebrow">{formatDateTime(new Date(p.ts).toISOString())}</div>
      <div className="mt-2 space-y-1.5">
        {p.actual !== undefined && (
          <div className="flex items-center gap-2.5">
            <span className="status-dot" style={{ background: COLOR_ACTUAL }} />
            <span className="font-medium">Actual</span>
            <span className="stat-figure text-text-faint">{p.actual.toFixed(1)}%</span>
          </div>
        )}
        {p.baseline !== undefined && (
          <div className="flex items-center gap-2.5">
            <span className="status-dot" style={{ background: COLOR_BASELINE }} />
            <span className="font-medium">Baseline</span>
            <span className="stat-figure text-text-faint">{p.baseline.toFixed(1)}%</span>
          </div>
        )}
        {p.scenario !== undefined && (
          <div className="flex items-center gap-2.5">
            <span className="status-dot" style={{ background: COLOR_SCENARIO }} />
            <span className="font-medium">Scenario</span>
            <span className="stat-figure text-text-faint">{p.scenario.toFixed(1)}%</span>
            {delta !== null && Math.abs(delta) >= 0.05 && (
              <span className="text-xs font-semibold" style={{ color: delta > 0 ? "var(--accent)" : "var(--ok)" }}>
                {delta > 0 ? "+" : ""}
                {delta.toFixed(1)}
              </span>
            )}
          </div>
        )}
        {p.bandLower !== undefined && p.bandUpper !== undefined && (
          <div className="text-xs text-text-faint">
            Scenario 80% interval: {p.bandLower.toFixed(1)}% – {p.bandUpper.toFixed(1)}%
          </div>
        )}
      </div>
    </div>
  );
}

function CapacityChart({ resource, params }: { resource: AvailableResource; params: CapacityParams }) {
  const { rows, nowTs, endTs, yMax } = useMemo(() => {
    const timeline = resource.timeline.filter((p) => p.timestamp);
    const nowMs = timeline.length ? new Date(timeline[0].timestamp as string).getTime() : 0;
    const out: ChartRow[] = resource.history.map((h) => ({ ts: new Date(h.timestamp).getTime(), actual: h.value }));
    for (const p of timeline) {
      out.push({
        ts: new Date(p.timestamp as string).getTime(),
        baseline: p.baseline,
        scenario: p.scenario,
        lower: p.scenario_lower,
        band: p.scenario_upper - p.scenario_lower,
        bandLower: p.scenario_lower,
        bandUpper: p.scenario_upper,
      });
    }
    out.sort((a, b) => a.ts - b.ts);
    const top = Math.max(100, resource.peak_scenario, ...timeline.map((p) => p.scenario_upper));
    return {
      rows: out,
      nowTs: nowMs,
      endTs: out.length ? out[out.length - 1].ts : nowMs,
      yMax: Math.ceil(top / 10) * 10,
    };
  }, [resource]);

  const vmsLandTs = params.extra_vms > 0 ? nowTs + params.start_day * 24 * HOUR_MS : null;
  const hardwareTs =
    params.compute_nodes_added > 0 || params.storage_gb_added > 0 ? nowTs + params.nodes_added_day * 24 * HOUR_MS : null;
  const extrapFrom =
    resource.first_extrapolated_hour !== null ? nowTs + resource.first_extrapolated_hour * HOUR_MS : null;
  const gradientId = `cap-grad-${resource.metric}`;

  return (
    <div className="h-[340px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 12, right: 16, bottom: 4, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLOR_SCENARIO} stopOpacity={0.28} />
              <stop offset="100%" stopColor={COLOR_SCENARIO} stopOpacity={0.08} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-soft)" vertical={false} />
          <XAxis
            dataKey="ts"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatTick}
            tick={{ fontSize: 11, fill: "var(--text-faint)" }}
            tickLine={false}
            axisLine={false}
            minTickGap={36}
          />
          <YAxis
            domain={[0, yMax]}
            tickFormatter={(v: number) => `${v}%`}
            tick={{ fontSize: 11, fill: "var(--text-faint)" }}
            tickLine={false}
            axisLine={false}
            width={44}
          />
          <Tooltip content={<ChartTooltip />} />
          {extrapFrom !== null && (
            <ReferenceArea x1={extrapFrom} x2={endTs} fill="var(--warn)" fillOpacity={0.08} ifOverflow="hidden" />
          )}
          {yMax > 100 && <ReferenceArea y1={100} y2={yMax} fill="var(--accent)" fillOpacity={0.05} ifOverflow="hidden" />}
          <ReferenceLine
            y={resource.threshold}
            stroke="var(--warn)"
            strokeDasharray="5 4"
            label={{ value: `${resource.threshold}% threshold`, position: "insideTopLeft", fontSize: 11, fill: "var(--warn)" }}
          />
          <ReferenceLine x={nowTs} stroke="var(--border)" label={{ value: "Now", position: "insideTopRight", fontSize: 11, fill: "var(--text-faint)" }} />
          {vmsLandTs !== null && vmsLandTs <= endTs && (
            <ReferenceLine
              x={vmsLandTs}
              stroke={COLOR_SCENARIO}
              strokeDasharray="2 3"
              label={{ value: "VMs land", position: "insideTopRight", fontSize: 11, fill: COLOR_SCENARIO }}
            />
          )}
          {hardwareTs !== null && hardwareTs <= endTs && (
            <ReferenceLine
              x={hardwareTs}
              stroke="var(--ok)"
              strokeDasharray="2 3"
              label={{ value: "Hardware in", position: "insideTopRight", fontSize: 11, fill: "var(--ok)" }}
            />
          )}
          {/* Scenario's 80% interval: transparent base up to `lower`, then
              the band on top -- the same stacked-area trick the Forecast
              page uses. */}
          <Area type="monotone" dataKey="lower" stackId="band" stroke="none" fill="transparent" isAnimationActive={false} connectNulls />
          <Area type="monotone" dataKey="band" stackId="band" stroke="none" fill={`url(#${gradientId})`} isAnimationActive={false} connectNulls />
          <Line type="monotone" dataKey="actual" stroke={COLOR_ACTUAL} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
          <Line
            type="monotone"
            dataKey="baseline"
            stroke={COLOR_BASELINE}
            strokeWidth={2}
            strokeDasharray="6 4"
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line type="monotone" dataKey="scenario" stroke={COLOR_SCENARIO} strokeWidth={2.5} dot={false} isAnimationActive={false} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------- result panels --
function VerdictBadge({ verdict }: { verdict: keyof typeof VERDICT_STYLE }) {
  const v = VERDICT_STYLE[verdict];
  return (
    <span
      className="inline-flex items-center rounded-[var(--radius-control)] px-2 py-0.5 text-[11px] font-semibold"
      style={{ color: v.color, background: `color-mix(in srgb, ${v.color} 12%, transparent)` }}
    >
      {v.label}
    </span>
  );
}

function BreachTile({
  title,
  color,
  label,
  sub,
}: {
  title: string;
  color: string;
  label: string;
  sub: string;
}) {
  return (
    <div className="panel border-l-[3px] p-3.5" style={{ borderLeftColor: color }}>
      <div className="eyebrow">{title}</div>
      <div className="stat-figure mt-1.5 text-base text-color-text">{label}</div>
      <div className="mt-0.5 text-xs text-text-faint">{sub}</div>
    </div>
  );
}

function ResourceDetail({ resource, params }: { resource: AvailableResource; params: CapacityParams }) {
  const horizon = params.horizon_days;
  const shift = resource.eta_shift_days;
  const shiftText =
    shift === null
      ? resource.verdict === "new_breach"
        ? "Baseline never crosses it"
        : resource.verdict === "relieved"
          ? "Scenario never crosses it"
          : "—"
      : Math.abs(shift) < 0.25
        ? "No meaningful change"
        : shift > 0
          ? `${formatDays(shift)} sooner than baseline`
          : `${formatDays(-shift)} later than baseline`;

  return (
    <div className="grid gap-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <BreachTile
          title="Baseline"
          color={COLOR_BASELINE}
          label={`Crosses ${resource.threshold}% ${breachLabel(resource.baseline_breach, horizon)}`}
          sub={`Peaks at ${resource.peak_baseline.toFixed(1)}% · ends at ${resource.end_baseline.toFixed(1)}%`}
        />
        <BreachTile
          title="Scenario"
          color={VERDICT_STYLE[resource.verdict].color}
          label={`Crosses ${resource.threshold}% ${breachLabel(resource.scenario_breach, horizon)}`}
          sub={`Peaks at ${resource.peak_scenario.toFixed(1)}% · ends at ${resource.end_scenario.toFixed(1)}%`}
        />
        <div className="panel p-3.5">
          <div className="eyebrow">Impact</div>
          <div className="mt-1.5">
            <VerdictBadge verdict={resource.verdict} />
          </div>
          <div className="mt-1.5 text-xs text-text-faint">{shiftText}</div>
        </div>
      </div>

      {resource.exceeds_capacity && (
        <div
          className="flex items-start gap-2 rounded-[var(--radius-control)] px-3 py-2 text-xs"
          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
        >
          <TriangleAlert className="mt-px h-3.5 w-3.5 flex-shrink-0" />
          <span>
            Projected demand exceeds 100% of capacity on at least one node — this load does not fit without more
            hardware (values are not clipped at 100%).
          </span>
        </div>
      )}

      {resource.notes.map((n) => (
        <div key={n} className="flex items-start gap-2 text-xs text-text-faint">
          <Info className="mt-px h-3.5 w-3.5 flex-shrink-0" />
          {n}
        </div>
      ))}

      <div className="panel overflow-x-auto p-0">
        <table className="w-full min-w-[520px] text-sm">
          <thead>
            <tr className="eyebrow text-left">
              <th className="px-4 py-2.5 font-medium">Node</th>
              <th className="px-3 py-2.5 text-right font-medium">Now</th>
              <th className="px-3 py-2.5 text-right font-medium">Baseline end</th>
              <th className="px-3 py-2.5 text-right font-medium">Scenario end</th>
              <th className="px-3 py-2.5 text-right font-medium">Hits {resource.threshold}%</th>
              <th className="px-4 py-2.5 text-right font-medium">Impact</th>
            </tr>
          </thead>
          <tbody>
            {resource.nodes.map((n) => (
              <tr key={n.hostname} style={{ borderTop: "1px solid var(--border-soft)" }}>
                <td className="px-4 py-2.5 font-medium text-color-text">{n.hostname}</td>
                <td className="stat-figure px-3 py-2.5 text-right text-text-dim">{n.current.toFixed(1)}%</td>
                <td className="stat-figure px-3 py-2.5 text-right text-text-dim">{n.baseline_end.toFixed(1)}%</td>
                <td className="stat-figure px-3 py-2.5 text-right text-color-text">{n.scenario_end.toFixed(1)}%</td>
                <td className="px-3 py-2.5 text-right text-xs text-text-dim">
                  {formatDays(n.baseline_eta_days)} → {formatDays(n.scenario_eta_days)}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <VerdictBadge verdict={n.verdict} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const RESERVATION_LABELS: { key: "vcpus" | "ram_mb" | "gigabytes"; label: string; fmt: (v: number) => string }[] = [
  { key: "vcpus", label: "vCPUs", fmt: (v) => Math.round(v).toLocaleString() },
  { key: "ram_mb", label: "RAM", fmt: (v) => `${(v / 1024).toFixed(0)} GB` },
  { key: "gigabytes", label: "Volumes", fmt: (v) => `${Math.round(v).toLocaleString()} GB` },
];

function ReservationBar({ row, fmt, label }: { row: ReservationRow; fmt: (v: number) => string; label: string }) {
  if (!row.available || row.pct_before == null || row.pct_after == null) {
    return <div className="text-xs text-text-faint">{label}: capacity unknown</div>;
  }
  const after = Math.min(row.pct_after, 100);
  const before = Math.min(row.pct_before, 100);
  return (
    <div className="grid gap-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-dim">{label}</span>
        <span className="stat-figure" style={{ color: row.oversold_after ? "var(--accent)" : "var(--text)" }}>
          {row.pct_before.toFixed(0)}% → {row.pct_after.toFixed(0)}%{row.oversold_after ? " · oversold" : ""}
        </span>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full" style={{ background: "var(--neutral-soft)" }}>
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{ width: `${after}%`, background: row.oversold_after ? "var(--accent)" : COLOR_SCENARIO, opacity: 0.45 }}
        />
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${before}%`, background: COLOR_BASELINE }} />
      </div>
      <div className="text-[11px] text-text-faint">
        {fmt(row.allocated_before ?? 0)} → {fmt(row.allocated_after ?? 0)} reserved of {fmt(row.sellable_after ?? 0)} sellable
      </div>
    </div>
  );
}

function ReservationPanel({ reservation }: { reservation: CapacityReservation }) {
  return (
    <div className="panel p-4">
      <div className="eyebrow flex items-center gap-1.5">
        <ServerCog className="h-3.5 w-3.5" />
        Can the scheduler place it? (reserved vs sellable)
      </div>
      {!reservation.available ? (
        <p className="mt-3 text-sm text-text-faint">{reservation.reason}</p>
      ) : (
        <>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {RESERVATION_LABELS.map(({ key, label, fmt }) => (
              <ReservationBar key={key} row={reservation.rows[key]} fmt={fmt} label={label} />
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-text-dim">
            {reservation.max_additional_vms_before !== null && (
              <span>
                Room for <span className="stat-figure text-color-text">{reservation.max_additional_vms_before}</span> more of this VM
                today
                {reservation.max_additional_vms_after !== null &&
                  reservation.max_additional_vms_after !== reservation.max_additional_vms_before && (
                    <>
                      , <span className="stat-figure text-color-text">{reservation.max_additional_vms_after}</span> with the added hardware
                    </>
                  )}
              </span>
            )}
            {reservation.showback_eur_month !== null && (
              <span className="inline-flex items-center gap-1">
                <Wallet className="h-3 w-3" />
                Showback share of these VMs:{" "}
                <span className="stat-figure text-color-text">€{reservation.showback_eur_month.toFixed(2)}</span>/month
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ page --
export default function CapacitySimulator() {
  const { data: options, error: optionsError } = useSWR<CapacityOptions>("/api/capacity/options", optionsFetcher, {
    revalidateOnFocus: false,
  });

  const defaults = useMemo<CapacityParams | null>(() => {
    if (!options) return null;
    return Object.fromEntries(
      Object.entries(options.parameters).map(([k, v]) => [k, v.default])
    ) as unknown as CapacityParams;
  }, [options]);

  const [params, setParams] = useState<CapacityParams | null>(null);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [metric, setMetric] = useState<CapacityMetric>("cpu_percent");

  const effective = params ?? defaults;
  const { data: result, error, pending } = useSimulation(effective);

  const set = <K extends keyof CapacityParams>(key: K, value: CapacityParams[K]) => {
    if (!effective) return;
    setActivePreset(null);
    setParams({ ...effective, [key]: value });
  };

  const applyPreset = (id: string) => {
    if (!defaults || !options) return;
    const preset = options.scenario_presets.find((p) => p.id === id);
    if (!preset) return;
    // Presets start from the defaults (not the current state) so they are
    // predictable, but keep the chosen horizon and threshold -- those are
    // how the person is looking at it, not part of the scenario.
    setParams({
      ...defaults,
      ...preset.params,
      horizon_days: effective?.horizon_days ?? defaults.horizon_days,
      threshold_pct: effective?.threshold_pct ?? defaults.threshold_pct,
    });
    setActivePreset(id);
  };

  const reset = () => {
    setParams(null);
    setActivePreset(null);
  };

  const resources = result?.resources ?? [];
  const selected = resources.find((r) => r.metric === metric);
  const firstAvailable = resources.find((r): r is AvailableResource => r.available);
  // A resource without forecast data has a disabled tab, so the one shown is
  // always an available one -- falling back to the first with data.
  const shownAvailable: AvailableResource | null = selected?.available ? selected : (firstAvailable ?? null);

  const changedCount = result?.changed_parameter_count ?? 0;
  const worst = result?.summary.worst_verdict ?? null;
  const bannerColor = worst ? VERDICT_STYLE[worst].color : "var(--neutral)";
  const isOk = worst === "ok" || worst === "relieved" || worst === null;

  if (optionsError) {
    return (
      <div className="panel p-6 text-sm" style={{ color: "var(--accent)" }}>
        Could not load simulator options: {optionsError.message}
      </div>
    );
  }
  if (!options || !effective || !defaults) {
    return (
      <p className="flex items-center gap-2 p-6 text-sm text-text-faint">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading simulator…
      </p>
    );
  }

  const bound = (k: keyof CapacityParams) => {
    const b = options.parameters[k];
    return { min: b.min ?? 0, max: b.max ?? 1_000_000 };
  };
  const isDefault = (k: keyof CapacityParams) => effective[k] === defaults[k];
  const horizonDays = effective.horizon_days;

  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-3">
          <div
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
            style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
          >
            <SlidersHorizontal className="h-[18px] w-[18px]" strokeWidth={1.75} />
          </div>
          <div>
            <h1 className="text-base font-semibold text-color-text">Capacity what-if</h1>
            <p className="text-xs text-text-faint">
              Test a scenario against the forecast — change several levers and read the combined effect on the capacity timeline.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-xs text-text-faint">
          {pending && (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Simulating…
            </span>
          )}
          <span
            className="rounded-[var(--radius-control)] px-2 py-1 font-semibold"
            style={{
              background: changedCount >= 2 ? "var(--accent-soft)" : "var(--neutral-soft)",
              color: changedCount >= 2 ? "var(--accent)" : "var(--text-faint)",
            }}
          >
            {changedCount} parameter{changedCount === 1 ? "" : "s"} changed
          </span>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* ---- LEFT: results ---- */}
        <div className="grid min-w-0 content-start gap-4">
          {error && (
            <div className="panel p-4 text-sm" style={{ color: "var(--accent)" }}>
              {error}
            </div>
          )}

          {result && (
            <div
              className="panel flex items-start gap-3 border-l-[3px] p-4"
              style={{ borderLeftColor: bannerColor, opacity: pending ? 0.7 : 1, transition: "opacity 120ms" }}
            >
              {isOk ? (
                <CircleCheck className="mt-0.5 h-5 w-5 flex-shrink-0" style={{ color: bannerColor }} />
              ) : (
                <TriangleAlert className="mt-0.5 h-5 w-5 flex-shrink-0" style={{ color: bannerColor }} />
              )}
              <div className="grid gap-1">
                <p className="text-sm font-medium text-color-text">{result.summary.headline}</p>
                {result.changed_parameters.length > 0 && (
                  <p className="text-xs text-text-faint">Changed: {result.changed_parameters.join(", ")}</p>
                )}
              </div>
            </div>
          )}

          <section className="panel p-4" style={{ opacity: pending ? 0.8 : 1, transition: "opacity 120ms" }}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div
                className="flex items-center gap-0.5 rounded-[var(--radius-control)] p-0.5"
                style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
                role="tablist"
              >
                {(result?.resources ?? options.resources.map((r) => ({ ...r, available: true as const }))).map((r) => {
                  const unavailable = "available" in r && r.available === false;
                  const active = shownAvailable?.metric === r.metric;
                  return (
                    <button
                      key={r.metric}
                      role="tab"
                      aria-selected={active}
                      disabled={unavailable}
                      title={unavailable && "reason" in r ? r.reason : undefined}
                      onClick={() => setMetric(r.metric)}
                      className="inline-flex items-center gap-1.5 rounded-[calc(var(--radius-control)-2px)] px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
                      style={active ? { background: "var(--accent)", color: "#fff" } : { color: "var(--text-faint)" }}
                    >
                      <MetricGlyph metric={r.metric} className="h-3.5 w-3.5" />
                      {r.label}
                    </button>
                  );
                })}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-faint">
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-4" style={{ background: COLOR_ACTUAL }} /> Actual
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-4 border-t-2 border-dashed" style={{ borderColor: COLOR_BASELINE }} /> Baseline
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-4" style={{ background: COLOR_SCENARIO }} /> Scenario
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-4 rounded" style={{ background: COLOR_SCENARIO, opacity: 0.3 }} /> 80% interval
                </span>
              </div>
            </div>

            <p className="mt-2 text-[11px] text-text-faint">
              Busiest {shownAvailable?.node_role ?? "compute"} node at each point — the first to saturate. Demand as a share of one
              node&apos;s capacity.
            </p>

            <div className="mt-3">
              {!result ? (
                <div className="flex h-[340px] items-center justify-center text-sm text-text-faint">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Running simulation…
                </div>
              ) : shownAvailable ? (
                <CapacityChart resource={shownAvailable} params={effective} />
              ) : (
                <div className="grid h-[200px] content-center justify-items-center gap-1 text-sm text-text-faint">
                  <span>No forecast data is available for any resource yet.</span>
                  {resources.map((r) => (!r.available ? <span key={r.metric} className="text-xs">{r.label}: {r.reason}</span> : null))}
                </div>
              )}
            </div>
            {shownAvailable?.first_extrapolated_hour != null && (
              <p className="mt-2 text-[11px]" style={{ color: "var(--warn)" }}>
                Shaded area: beyond the ML model&apos;s trained range — the baseline there is extrapolated and less certain.
              </p>
            )}
          </section>

          {shownAvailable && <ResourceDetail resource={shownAvailable} params={effective} />}

          {result && <ReservationPanel reservation={result.reservation} />}

          {result && (
            <div className="panel p-4">
              <div className="eyebrow flex items-center gap-1.5">
                <Info className="h-3.5 w-3.5" />
                How this is computed
              </div>
              <ul className="mt-2 grid gap-1 text-xs text-text-dim">
                {result.assumptions.map((a) => (
                  <li key={a}>• {a}</li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-text-faint">
                Baseline: {result.models.length ? result.models.join(", ") : "no model"} forecast
                {result.capacity_sampled_at ? ` · capacity as of ${formatDateTime(result.capacity_sampled_at)}` : ""}.
              </p>
            </div>
          )}
        </div>

        {/* ---- RIGHT: controls ---- */}
        <aside className="panel grid content-start gap-5 p-4 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <div className="eyebrow flex items-center gap-1.5">
                <Gauge className="h-3.5 w-3.5" />
                Scenarios
              </div>
              <button
                onClick={reset}
                className="inline-flex items-center gap-1 rounded-[var(--radius-control)] px-2 py-1 text-xs font-medium text-text-dim transition-colors hover:bg-[var(--canvas)]"
              >
                <RotateCcw className="h-3 w-3" /> Reset
              </button>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {options.scenario_presets.map((p) => (
                <button
                  key={p.id}
                  onClick={() => applyPreset(p.id)}
                  title={p.description}
                  className="rounded-[var(--radius-control)] px-2.5 py-1.5 text-xs font-medium transition-colors"
                  style={
                    activePreset === p.id
                      ? { background: "var(--accent)", color: "#fff" }
                      : { border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text-dim)" }
                  }
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <Group icon={ServerCog} title="New VMs">
            <Param label="Extra VMs" value={effective.extra_vms} onChange={(v) => set("extra_vms", v)} {...bound("extra_vms")} sliderMax={100} isDefault={isDefault("extra_vms")} />
            <div className="grid gap-1">
              <span className="text-xs text-text-faint">Size</span>
              <div className="flex flex-wrap gap-1.5">
                {options.flavor_presets.map((f) => {
                  const active =
                    effective.vm_vcpus === f.vm_vcpus && effective.vm_ram_gb === f.vm_ram_gb && effective.vm_disk_gb === f.vm_disk_gb;
                  return (
                    <button
                      key={f.name}
                      onClick={() => {
                        setActivePreset(null);
                        setParams({ ...effective, vm_vcpus: f.vm_vcpus, vm_ram_gb: f.vm_ram_gb, vm_disk_gb: f.vm_disk_gb });
                      }}
                      title={`${f.vm_vcpus} vCPU · ${f.vm_ram_gb} GB RAM · ${f.vm_disk_gb} GB disk`}
                      className="rounded-[var(--radius-control)] px-2.5 py-1 text-xs font-medium transition-colors"
                      style={
                        active
                          ? { background: "var(--medium)", color: "#fff" }
                          : { border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text-dim)" }
                      }
                    >
                      {f.name}
                    </button>
                  );
                })}
              </div>
            </div>
            <Param label="vCPUs per VM" value={effective.vm_vcpus} onChange={(v) => set("vm_vcpus", v)} {...bound("vm_vcpus")} sliderMax={16} isDefault={isDefault("vm_vcpus")} />
            <Param label="RAM per VM" unit="GB" value={effective.vm_ram_gb} onChange={(v) => set("vm_ram_gb", v)} {...bound("vm_ram_gb")} min={1} sliderMax={64} isDefault={isDefault("vm_ram_gb")} />
            <Param label="Disk per VM" unit="GB" value={effective.vm_disk_gb} onChange={(v) => set("vm_disk_gb", v)} {...bound("vm_disk_gb")} sliderMax={500} step={10} isDefault={isDefault("vm_disk_gb")} />
          </Group>

          <Group icon={CalendarClock} title="Timing">
            <Param
              label="Arrive in"
              unit="days"
              value={effective.start_day}
              onChange={(v) => set("start_day", v)}
              {...bound("start_day")}
              sliderMax={horizonDays}
              isDefault={isDefault("start_day")}
              hint="Days from now until the first VMs start up."
            />
            <Param
              label="Roll in over"
              unit="days"
              value={effective.ramp_days}
              onChange={(v) => set("ramp_days", v)}
              {...bound("ramp_days")}
              sliderMax={Math.min(horizonDays, 60)}
              isDefault={isDefault("ramp_days")}
              hint="0 = all at once; otherwise a linear ramp (a project onboarding in waves)."
            />
          </Group>

          <Group icon={TrendingUp} title="Growth & hardware">
            <Param
              label="Organic growth"
              unit="%/mo"
              value={effective.workload_growth_pct_per_month}
              onChange={(v) => set("workload_growth_pct_per_month", v)}
              {...bound("workload_growth_pct_per_month")}
              sliderMax={100}
              isDefault={isDefault("workload_growth_pct_per_month")}
              hint="Compounding growth of the load that already exists."
            />
            <Param label="Compute nodes added" value={effective.compute_nodes_added} onChange={(v) => set("compute_nodes_added", v)} {...bound("compute_nodes_added")} sliderMax={6} isDefault={isDefault("compute_nodes_added")} />
            <Param label="Storage added" unit="GB" value={effective.storage_gb_added} onChange={(v) => set("storage_gb_added", v)} {...bound("storage_gb_added")} sliderMax={5000} step={100} isDefault={isDefault("storage_gb_added")} />
            <Param
              label="Hardware in service in"
              unit="days"
              value={effective.nodes_added_day}
              onChange={(v) => set("nodes_added_day", v)}
              {...bound("nodes_added_day")}
              sliderMax={horizonDays}
              isDefault={isDefault("nodes_added_day")}
            />
          </Group>

          <Group icon={Gauge} title="View">
            <Param label="Threshold" unit="%" value={effective.threshold_pct} onChange={(v) => set("threshold_pct", v)} {...bound("threshold_pct")} min={50} isDefault={isDefault("threshold_pct")} hint="Utilisation counted as “full”." />
            <div className="grid gap-1">
              <span className="text-xs text-text-faint">Horizon</span>
              <div
                className="flex items-center gap-0.5 rounded-[var(--radius-control)] p-0.5"
                style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              >
                {options.horizons_days.map((d) => (
                  <button
                    key={d}
                    onClick={() => setParams({ ...effective, horizon_days: d })}
                    className="flex-1 rounded-[calc(var(--radius-control)-2px)] py-1.5 text-xs font-medium transition-colors"
                    style={effective.horizon_days === d ? { background: "var(--medium)", color: "#fff" } : { color: "var(--text-faint)" }}
                  >
                    {d}d
                  </button>
                ))}
              </div>
            </div>
          </Group>

          <details className="group">
            <summary className="eyebrow cursor-pointer select-none">Utilisation assumptions</summary>
            <div className="mt-3 grid gap-3">
              <p className="text-[11px] leading-snug text-text-faint">
                How hard each new VM actually works. Reserved size drives placement; what it <em>uses</em> drives hypervisor load.
              </p>
              <Param label="Avg CPU use" unit="%" value={effective.vm_cpu_util_pct} onChange={(v) => set("vm_cpu_util_pct", v)} {...bound("vm_cpu_util_pct")} isDefault={isDefault("vm_cpu_util_pct")} />
              <Param label="Avg memory use" unit="%" value={effective.vm_mem_util_pct} onChange={(v) => set("vm_mem_util_pct", v)} {...bound("vm_mem_util_pct")} isDefault={isDefault("vm_mem_util_pct")} />
              <Param label="Volume fill" unit="%" value={effective.vm_disk_fill_pct} onChange={(v) => set("vm_disk_fill_pct", v)} {...bound("vm_disk_fill_pct")} isDefault={isDefault("vm_disk_fill_pct")} hint="Below 100 if volumes are thin-provisioned." />
            </div>
          </details>
        </aside>
      </div>
    </main>
  );
}
