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

/** One (weekday, hour) slot of a node/metric's learned baseline curve, as
 * returned by GET /api/v1/baselines/{hostname}?metric_name=. weekday is
 * 0=Monday .. 6=Sunday, hour is 0-23. median/mad is the robust estimator
 * used for anomaly scoring (ADR-0001); mean/stddev is included alongside
 * for comparison only. */
export interface BaselineSlot {
  weekday: number;
  hour: number;
  median: number;
  mad: number;
  mean: number;
  stddev: number;
  sample_count: number;
  updated_at: string | null;
}
export interface ForecastPoint {
  day: "tomorrow" | "7_days" | "30_days";
  value: number;
}

export interface ForecastResult {
  hostname: string;
  metric: string;
  forecast: ForecastPoint[];
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

// --- Alert correlation (Phase 6 -- see routers/anomalies.py's /incidents,
// services/alert_correlation.py) ------------------------------------------
//
// One incident per GET /api/v1/anomalies/incidents entry. Every open
// AnomalyFlag comes back nested under exactly one incident -- an alert
// with no correlated peer is still an incident, just with
// member_count === 1, so AlertsView.tsx can group by incident_id
// uniformly instead of special-casing "no incident".

export interface AnomalyIncidentRootCause {
  vertex_id: string;
  label: TopologyVertexLabel | null;
}

export interface AnomalyIncidentGraphPath {
  vertex_ids: string[];
  edges: { type: TopologyEdgeType; source: string; target: string }[];
}

export interface AnomalyIncident {
  incident_id: string;
  severity: AnomalySeverity;
  member_count: number;
  root_cause_guess: AnomalyIncidentRootCause | null;
  narrative: string;
  members: AnomalyFlag[];
  graph_path: AnomalyIncidentGraphPath | null;
}

// --- RCA suggestions (see services/api/app/routers/anomalies.py's /rca,
// services/api/app/services/rca_suggester.py) ------------------------------
//
// One entry per pair of currently-anomalous, graph-adjacent vertices from
// GET /api/v1/anomalies/rca. Unlike AnomalyIncident (which just groups
// alerts), each suggestion is a directed "X caused Y" claim: `text` is the
// full sentence the API already composed, and always names `relationship`
// -- never just metric names -- per the feature's acceptance criterion.

export interface RcaEndpoint {
  id: string;
  label: TopologyVertexLabel | null;
  metric_name: string;
  severity: AnomalySeverity;
}

export interface RcaSuggestion {
  cause: RcaEndpoint;
  effect: RcaEndpoint;
  relationship: TopologyEdgeType;
  text: string;
}

// --- Topology (Phase 6 -- see services/api/app/routers/topology.py) -------
//
// Mirrors schemas.TopologyGraphOut/TopologyVertexDetailOut/TopologyHealthOut
// on the API side. `properties` is left as a loose dict rather than typed
// per-label (the graph has six vertex labels -- Node/Service/Network/
// Subnet/Router/FloatingIP -- each with its own property shape; see
// graph_db.py's module docstring) since the frontend only needs a handful
// of well-known keys (role, state, hostname, ...) off of it, read
// defensively via lib/topology.ts's helpers.

export type TopologyVertexLabel = "Node" | "Service" | "Network" | "Subnet" | "Router" | "FloatingIP";

export type TopologyEdgeType = "RUNS_ON" | "SERVES" | "CONNECTS";

export interface TopologyVertex {
  id: string;
  label: TopologyVertexLabel;
  properties: Record<string, unknown>;
}

export interface TopologyEdge {
  source: string;
  target: string;
  type: TopologyEdgeType;
}

export interface TopologyGraph {
  nodes: TopologyVertex[];
  edges: TopologyEdge[];
}

export interface TopologyNeighbor {
  id: string | null;
  label: TopologyVertexLabel | null;
  relationship: TopologyEdgeType;
  direction: "incoming" | "outgoing";
}

export interface TopologyVertexDetail {
  id: string;
  label: TopologyVertexLabel;
  properties: Record<string, unknown>;
  neighbors: TopologyNeighbor[];
}

export type TopologySyncType = "openstack" | "prometheus_health";
export type TopologySyncStatus = "ok" | "degraded" | "failed" | "unknown";

export interface TopologySyncRun {
  sync_type: string;
  status: string;
  summary: Record<string, unknown> | null;
  error: string | null;
  started_at: string; // ISO 8601
  finished_at: string; // ISO 8601
}

export interface TopologyHealth {
  status: TopologySyncStatus;
  syncs: Record<string, TopologySyncRun | null>;
}

// -- Knowledge copilot (adr-0005) ------------------------------------------

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatSource {
  source_path: string;
  doc_title: string;
  heading: string | null;
  score: number;
}

// -- Network health panel (story 3.6 -- see services/api/app/routers/network.py) --
//
// Mirrors schemas.NetworkHealthOut/NetworkLatencyOut on the API side.
// routers_down/floating_ips_orphaned/ports_down are left as loose dicts
// (same reasoning as TopologyVertex.properties in the topology block
// above) since the panel only needs a handful of well-known keys
// (id, status, name, ...) off each, read defensively.

export type NetworkHealthStatus = "ok" | "degraded";

export interface NetworkLatency {
  hostname: string;
  ip_address: string | null;
  port: number;
  latency_ms: number | null;
  reachable: boolean;
  error: string | null;
}

export interface NetworkHealth {
  status: NetworkHealthStatus;
  graph_available: boolean;
  routers_down: Record<string, unknown>[];
  floating_ips_orphaned: Record<string, unknown>[];
  ports_down: Record<string, unknown>[];
  latencies: NetworkLatency[];
}
