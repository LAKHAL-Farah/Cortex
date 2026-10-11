/** Types and helpers for the capacity what-if simulator (roadmap 4.12).
 * Shapes mirror services/api/app/services/capacity_simulator.py::simulate
 * and routers/capacity.py. The parameter defaults and bounds are NOT
 * duplicated here: the page reads them from GET /api/v1/capacity/options so
 * the sliders and the API's validation can't drift apart. */

export type CapacityMetric = "cpu_percent" | "memory_percent" | "disk_percent";

/** Every lever of a scenario. Same names as the API's ScenarioIn. */
export interface CapacityParams {
  extra_vms: number;
  vm_vcpus: number;
  vm_ram_gb: number;
  vm_disk_gb: number;
  vm_cpu_util_pct: number;
  vm_mem_util_pct: number;
  vm_disk_fill_pct: number;
  start_day: number;
  ramp_days: number;
  workload_growth_pct_per_month: number;
  compute_nodes_added: number;
  storage_gb_added: number;
  nodes_added_day: number;
  threshold_pct: number;
  horizon_days: number;
}

export interface ParamBounds {
  min: number | null;
  max: number | null;
  default: number;
  description: string | null;
}

export interface FlavorPreset {
  name: string;
  vm_vcpus: number;
  vm_ram_gb: number;
  vm_disk_gb: number;
}

export interface ScenarioPreset {
  id: string;
  label: string;
  description: string;
  params: Partial<CapacityParams>;
}

export interface CapacityOptions {
  parameters: Record<keyof CapacityParams, ParamBounds>;
  flavor_presets: FlavorPreset[];
  scenario_presets: ScenarioPreset[];
  horizons_days: number[];
  resources: { metric: CapacityMetric; label: string; node_role: string }[];
}

export type CapacityVerdict =
  | "ok"
  | "new_breach"
  | "earlier_breach"
  | "unchanged"
  | "later_breach"
  | "relieved"
  | "already_breached";

export interface BreachInfo {
  will_breach: boolean;
  already_breached: boolean;
  eta_hours: number | null;
  eta_days: number | null;
  crossing_timestamp: string | null;
}

export interface CapacityTimelinePoint {
  horizon_hours: number;
  timestamp: string | null;
  baseline: number;
  baseline_lower: number;
  baseline_upper: number;
  scenario: number;
  scenario_lower: number;
  scenario_upper: number;
}

export interface CapacityNodeRow {
  hostname: string;
  current: number;
  baseline_end: number;
  scenario_end: number;
  baseline_peak: number;
  scenario_peak: number;
  baseline_eta_days: number | null;
  scenario_eta_days: number | null;
  verdict: CapacityVerdict;
}

export type CapacityResource =
  | { metric: CapacityMetric; label: string; node_role: string; available: false; reason: string }
  | {
      metric: CapacityMetric;
      label: string;
      node_role: string;
      available: true;
      node_count: number;
      threshold: number;
      vm_load_pct_points: number;
      capacity_scale_after: number;
      notes: string[];
      first_extrapolated_hour: number | null;
      history: { timestamp: string; value: number }[];
      timeline: CapacityTimelinePoint[];
      baseline_breach: BreachInfo;
      scenario_breach: BreachInfo;
      /** Days sooner (positive) or later (negative) the scenario crosses
       * the threshold; null unless both cases cross it. */
      eta_shift_days: number | null;
      verdict: CapacityVerdict;
      end_baseline: number;
      end_scenario: number;
      peak_baseline: number;
      peak_scenario: number;
      exceeds_capacity: boolean;
      nodes: CapacityNodeRow[];
    };

export interface ReservationRow {
  available: boolean;
  sellable_before?: number;
  sellable_after?: number;
  allocated_before?: number;
  allocated_after?: number;
  pct_before?: number | null;
  pct_after?: number | null;
  oversold_after?: boolean;
}

export type CapacityReservation =
  | { available: false; reason: string }
  | {
      available: true;
      rows: { vcpus: ReservationRow; ram_mb: ReservationRow; gigabytes: ReservationRow };
      max_additional_vms_before: number | null;
      max_additional_vms_after: number | null;
      oversold_after: boolean;
      showback_eur_month: number | null;
    };

export interface CapacityResult {
  scenario: CapacityParams;
  changed_parameters: (keyof CapacityParams)[];
  changed_parameter_count: number;
  horizon_days: number;
  summary: {
    headline: string;
    worst_verdict: CapacityVerdict | null;
    worst_resource: CapacityMetric | null;
    first_breach: ({ metric: CapacityMetric; label: string } & BreachInfo) | null;
  };
  resources: CapacityResource[];
  reservation: CapacityReservation;
  assumptions: string[];
  capacity_sampled_at: string | null;
  models: string[];
}

export const VERDICT_STYLE: Record<CapacityVerdict, { label: string; color: string }> = {
  ok: { label: "Stays below threshold", color: "var(--ok)" },
  new_breach: { label: "New breach", color: "var(--accent)" },
  earlier_breach: { label: "Breaches sooner", color: "var(--accent)" },
  unchanged: { label: "Breach date unchanged", color: "var(--warn)" },
  later_breach: { label: "Breaches later", color: "var(--warn)" },
  relieved: { label: "Breach avoided", color: "var(--ok)" },
  already_breached: { label: "Already over threshold", color: "var(--accent)" },
};

/** "in ~N days" wording, same conventions as lib/thresholds.ts's
 * thresholdEtaLabel so the simulator and the Forecast page read alike. */
export function breachLabel(b: BreachInfo, horizonDays: number): string {
  if (b.already_breached) return "already over threshold";
  if (!b.will_breach) return `not within ${horizonDays} days`;
  const days = b.eta_days ?? 0;
  if (days < 1) return "in <1 day";
  const rounded = Math.round(days);
  return rounded === 1 ? "in ~1 day" : `in ~${rounded} days`;
}

export function formatDays(days: number | null): string {
  if (days === null) return "—";
  return days < 1 ? "<1 d" : `${Math.round(days)} d`;
}
