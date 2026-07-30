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