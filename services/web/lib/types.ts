export type NodeRole = "controller" | "compute" | "storage" | "monitoring";

export interface Node {
  id: string;
  hostname: string;
  ip_address: string;
  role: NodeRole;
  exporter_port: number;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface LiveMetrics {
  node: string;
  role: string;
  instance: string;
  cpu_percent: number;
  memory_percent: number;
  swap_percent: number;
  disk_percent: number;
  disk_read: string;
  disk_write: string;
  network_rx: string;
  network_tx: string;
  load1: number;
  load5: number;
  load15: number;
  uptime: string;
  status: "up" | "down";
  health: "healthy" | "warning" | "critical";
  procs_running: number;
  procs_blocked: number;
}

export interface DashboardNode extends Node {
  instance: string;
  has_metrics: boolean;
  metrics: LiveMetrics | null;
}

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";

export interface LogEntry {
  ts: number; // unix ms
  line: string;
  host: string | null;
  role: string | null;
  source: string | null; // Loki "job" label: "system" (syslog) or a service name
  service: string | null;
}

export type AnomalySeverity = "medium" | "high" | "critical";
export type AnomalyMethod = "robust_zscore" | "ewma_fallback";

export interface AnomalyFlag {
  hostname: string;
  metric_name: string; // e.g. "cpu_usage" | "ram_usage"
  current_value: number;
  z_score: number;
  severity: AnomalySeverity;
  method: AnomalyMethod;
  baseline_n: number | null; // sample count backing the baseline (null when EWMA fallback)
  detected_at: string; // ISO 8601
}

/** One row per anomaly episode (Alerts > History), as opposed to AnomalyFlag
 * which only ever reflects the current state per host/metric. */
export interface AnomalyEvent {
  id: string;
  hostname: string;
  metric_name: string;
  current_value: number; // peak value reached during the episode
  z_score: number; // peak z-score reached during the episode
  severity: AnomalySeverity; // peak severity reached during the episode
  method: AnomalyMethod;
  baseline_n: number | null;
  started_at: string; // ISO 8601
  resolved_at: string | null; // ISO 8601, null while still active
  is_active: boolean;
}