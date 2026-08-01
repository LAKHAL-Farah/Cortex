import type { BaselineSlot } from "./types";

/** Metrics the baseline builder currently tracks (services/api/app/services/baseline_builder.py).
 * Kept as an explicit list — same approach as METRIC_LABEL in lib/anomalies.ts — so the
 * selector doesn't have to guess valid metric_name values before any data comes back. */
export const BASELINE_METRICS = ["cpu_usage", "ram_usage"] as const;
export type BaselineMetric = (typeof BASELINE_METRICS)[number];

// weekday is 0=Monday .. 6=Sunday per services/api/app/models.py::Baseline
export const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export const WEEKDAY_LABELS_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const HOURS = Array.from({ length: 24 }, (_, i) => i);

/** JS Date.getDay() is 0=Sunday..6=Saturday; the baseline table is 0=Monday..6=Sunday.
 * This converts the former to the latter, e.g. to default-select "today". */
export function jsDayToWeekday(jsDay: number): number {
  return (jsDay + 6) % 7;
}

/** Compact hour label for axis ticks / grid headers, e.g. 0 -> "12a", 13 -> "1p". */
export function hourLabel(hour: number): string {
  if (hour === 0) return "12a";
  if (hour === 12) return "12p";
  return hour < 12 ? `${hour}a` : `${hour - 12}p`;
}

export function slotKey(weekday: number, hour: number): string {
  return `${weekday}-${hour}`;
}

/** Index a flat slot list by "weekday-hour" for O(1) grid lookups. */
export function indexSlots(slots: BaselineSlot[]): Map<string, BaselineSlot> {
  const map = new Map<string, BaselineSlot>();
  for (const s of slots) map.set(slotKey(s.weekday, s.hour), s);
  return map;
}

/** Share of the 168 (7 x 24) weekly slots that have a computed baseline yet. */
export function coverage(slots: BaselineSlot[]): number {
  return Math.round((slots.length / 168) * 100);
}

export function totalSamples(slots: BaselineSlot[]): number {
  return slots.reduce((sum, s) => sum + (s.sample_count ?? 0), 0);
}

/** 0..1 fill for heatmap cell intensity, clamped against a scale a bit past
 * 100 so a single spiky slot doesn't wash out the rest of the grid. */
export function cellIntensity(value: number, max = 100): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(Math.max(value / max, 0), 1);
}

export function mostRecentUpdate(slots: BaselineSlot[]): string | null {
  let latest: string | null = null;
  for (const s of slots) {
    if (s.updated_at && (!latest || s.updated_at > latest)) latest = s.updated_at;
  }
  return latest;
}

/** Deterministic, sentence-form summary of a baseline curve — same idea as
 * buildInsight() in lib/anomalies.ts, just describing the learned pattern
 * itself rather than a single deviation from it. */
export function buildBaselineInsight(hostname: string, metricLabel: string, slots: BaselineSlot[]): string {
  if (!slots.length) {
    return `No baseline has been learned yet for ${hostname}'s ${metricLabel.toLowerCase()} — it needs more history before a normal-usage curve can be computed.`;
  }

  const peak = slots.reduce((a, b) => (b.median > a.median ? b : a));
  const trough = slots.reduce((a, b) => (b.median < a.median ? b : a));
  const cov = coverage(slots);
  const samples = totalSamples(slots);

  return (
    `${hostname}'s ${metricLabel.toLowerCase()} typically peaks around ${WEEKDAY_LABELS_LONG[peak.weekday]} ` +
    `${hourLabel(peak.hour)} at ${peak.median.toFixed(1)}%, and dips to ${trough.median.toFixed(1)}% around ` +
    `${WEEKDAY_LABELS_LONG[trough.weekday]} ${hourLabel(trough.hour)}. This is based on ${samples.toLocaleString()} ` +
    `samples across ${cov}% of the weekly cycle.`
  );
}
