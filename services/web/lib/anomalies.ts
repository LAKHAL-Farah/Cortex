import type { AnomalyFlag, AnomalyMethod, AnomalySeverity } from "./types";

export const ALL_SEVERITIES: AnomalySeverity[] = ["critical", "high", "medium"];

export const SEVERITY_COLOR: Record<AnomalySeverity, string> = {
  critical: "var(--crit)",
  high: "var(--warn)",
  medium: "var(--medium)",
};

export const SEVERITY_SOFT: Record<AnomalySeverity, string> = {
  critical: "var(--crit-soft)",
  high: "var(--warn-soft)",
  medium: "var(--medium-soft)",
};

export const SEVERITY_LABEL: Record<AnomalySeverity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
};

// Same 2 / 3 / 4 robust-sigma thresholds the detector uses to assign severity
// (see services/api/app/services/anomaly_detector.py::THRESHOLDS). Kept here
// so the UI's z-score meter lines up with what actually produced the flag.
export const SEVERITY_THRESHOLDS: Record<AnomalySeverity, number> = {
  medium: 2,
  high: 3,
  critical: 4,
};

export const METHOD_LABEL: Record<AnomalyMethod, string> = {
  robust_zscore: "Robust z-score",
  ewma_fallback: "EWMA fallback",
};

const METRIC_LABEL: Record<string, string> = {
  cpu_usage: "CPU usage",
  ram_usage: "Memory usage",
  service_state: "Service state",
};

/** Human label for a metric_name, falling back to a de-slugged version for
 * metrics the detector adds later that the UI doesn't know about yet. */
export function metricLabel(metric: string): string {
  return METRIC_LABEL[metric] ?? metric.replace(/_/g, " ");
}

/** "service_state" flags come from the OpenStack/Prometheus state
 * cross-check (see services/api/app/services/prometheus_health.py), not
 * from anomaly_detector.py's baseline/EWMA scoring -- current_value,
 * z_score, method and baseline_n on these rows are placeholders (1.0 /
 * 0.0 / "robust_zscore" / 1), not a real statistical read. The UI needs
 * to know this so it doesn't present a fabricated percentage/sigma/
 * baseline-sample story for what is actually just "this service isn't
 * in its expected running state right now". */
export function isServiceStateMetric(metric: string): boolean {
  return metric === "service_state";
}

/** A service_state AnomalyFlag/AnomalyEvent's `hostname` is actually a
 * :Service vertex id in the `{binary}@{host}` shape topology_sync.py
 * mints (see prometheus_health.py::_sync_service_state_anomalies) --
 * NOT a real hostname. Splits it back into its parts so the UI can show
 * "Nova compute, running on compute1-sim" instead of the raw id, or null
 * if it doesn't look like a service id at all (defensive -- every
 * service_state flag should match, but nothing stops a future metric
 * from reusing the same id shape by coincidence).
 */
export function parseServiceId(id: string): { binary: string; host: string } | null {
  const at = id.indexOf("@");
  if (at <= 0 || at === id.length - 1) return null;
  return { binary: id.slice(0, at), host: id.slice(at + 1) };
}

/** Mirrors rca_suggester's neighbor + alert_correlation.py's
 * `_prettify_service_name` on the API side: "nova-compute" -> "Nova
 * compute". Falls back to the raw binary (or the full id, via
 * parseServiceId returning null) when there's nothing to prettify. */
export function serviceDisplayName(id: string): string {
  const parsed = parseServiceId(id);
  if (!parsed) return id;
  return parsed.binary.replace(/[-_]/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function formatZScore(z: number): string {
  const sign = z > 0 ? "+" : "";
  return `${sign}${z.toFixed(2)}σ`;
}

/** 0..1 fill for the z-score meter, clamped against a scale a bit past the
 * critical threshold so bars stay comparable instead of pegging at 100%. */
export function zScoreFill(z: number): number {
  const scale = SEVERITY_THRESHOLDS.critical + 1.5;
  return Math.min(Math.abs(z) / scale, 1);
}

export function formatDetectedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

/** Compact "3m ago" style relative time, consistent with lib/logs.ts. */
export function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "—";
  const diffSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.round(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.round(diffHour / 24);
  return `${diffDay}d ago`;
}

/** How long an anomaly episode has lasted: startIso -> endIso, or "now" while
 * still active (endIso == null). Used on the Alerts > History page. */
export function formatDuration(startIso: string, endIso: string | null): string {
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return "—";
  const diffSec = Math.max(0, Math.round((end - start) / 1000));
  if (diffSec < 60) return `${diffSec}s`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ${diffMin % 60}m`;
  const diffDay = Math.floor(diffHour / 24);
  return `${diffDay}d ${diffHour % 24}h`;
}

/** One-line, deterministic summary of a flag built from its own fields —
 * not a model call, just the anomaly's numbers put into a sentence so the
 * drawer reads like an explanation instead of a raw record dump. */
export function buildInsight(a: AnomalyFlag): string {
  if (isServiceStateMetric(a.metric_name)) {
    // Live down/unreachable detection, not a deviation from a baseline --
    // no %, no sigma, no "baseline of N samples" framing here.
    const parsed = parseServiceId(a.hostname);
    const subject = parsed ? `${serviceDisplayName(a.hostname)} on ${parsed.host}` : a.hostname;
    return `${subject} was flagged ${a.severity} because it isn't reporting its expected running state right now — this is a live service state check, not a statistical metric comparison.`;
  }

  const metric = metricLabel(a.metric_name);
  const magnitude = Math.abs(a.z_score).toFixed(1);
  const startedAt = new Date(a.detected_at).toLocaleString();

  if (a.method === "ewma_fallback") {
    const direction = a.z_score >= 0 ? "above" : "below";

    return `${metric} on ${a.hostname} reached ${a.current_value.toFixed(1)}% on ${startedAt}, ${magnitude}σ ${direction} its recent expected level. There was not enough historical data for this weekday and hour, so a short-term EWMA estimate was used.`;
  }

  const direction =
    a.z_score >= 0
      ? "well above the normal level expected at this time"
      : "well below the normal level expected at this time";

  return `${metric} on ${a.hostname} reached ${a.current_value.toFixed(1)}% on ${startedAt}, ${direction}. It was ${magnitude}σ from the contextual baseline, based on ${a.baseline_n ?? "—"} historical samples for the same weekday and hour.`;
}
