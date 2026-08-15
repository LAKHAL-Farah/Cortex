import type { ThresholdWarning } from "@/lib/types";

/** "X will hit threshold in ~N days" -- N rounded to whole days for
 * readability, with "already over" / "<1 day" spelled out for the
 * near-term edge cases rather than showing "~0 days". Shared by
 * ForecastExplorer (per-resource) and ThresholdWarningsPanel (fleet-wide)
 * so the wording stays identical wherever a warning shows up. */
export function thresholdEtaLabel(warning: ThresholdWarning): string {
  if (warning.already_breached) return "already over threshold";
  const days = warning.eta_days ?? 0;
  if (days < 1) return "in <1 day";
  const rounded = Math.round(days);
  if (rounded === 1) return "in ~1 day";
  return `in ~${rounded} days`;
}

export function metricLabel(metric: string): string {
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
