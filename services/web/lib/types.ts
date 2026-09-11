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
  details?: { source_ips?: string[] | null; triggered_by?: string | null } | null;
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
  horizon_hours: number;
  timestamp: string;
  predicted: number;
  lower: number;
  upper: number;
  /** True (2.8) when this point falls past whatever horizon the ML model
   * actually had training support for, and was instead produced by the
   * widening seasonal-persistence extension -- lets the UI mark the part of
   * a 30/90-day forecast that's genuinely less certain than the rest. */
  extrapolated: boolean;
}

export interface ForecastActualPoint {
  timestamp: string;
  value: number;
}

/** Selectable forecast horizons (2.8: "Extend forecast horizon to 30/90
 * days"). Passed as `?horizon_days=` to the API. */
export const FORECAST_HORIZON_DAYS = [7, 30, 90] as const;
export type ForecastHorizonDays = (typeof FORECAST_HORIZON_DAYS)[number];

export interface ForecastResult {
  hostname: string;
  metric: string;
  /** "ml_quantile" when there's enough recent history to trust the pooled
   * quantile model, "fallback_seasonal_persistence" for hosts too new/thin
   * for that -- surfaced so the UI can label a fallback forecast as such.
   * Still "ml_quantile" (2.8) when only *some* points -- the ones within the
   * model's training-supported range -- actually used it; check each
   * point's `extrapolated` flag for that detail. */
  model_type: "ml_quantile" | "fallback_seasonal_persistence";
  generated_at: string;
  n_points_used: number;
  /** Echoes the requested horizon (2.8), clamped to [1, 90]. */
  horizon_days: number;
  /** Horizon, in hours, of the furthest-out point actually served. */
  max_horizon_hours: number;
  /** Hourly resolution for the first 24h, then daily-to-fortnightly
   * checkpoints out to `horizon_days` (2.8: up to 90). */
  forecast: ForecastPoint[];
  /** Recent hourly-resampled actuals, for the "prediction vs actual" chart. */
  actual: ForecastActualPoint[];
}

// -- Threshold-breach ETA (2.5: "X will hit threshold in ~N days") --------
//
// GET /api/v1/forecast/{hostname}/{metric}/threshold returns one of these;
// GET /api/v1/forecast/warnings returns a list, already filtered to
// will_breach === true and sorted soonest-first (see
// forecast_service.list_threshold_warnings on the API side).

export interface ThresholdWarning {
  hostname: string;
  metric: string;
  model_type: "ml_quantile" | "fallback_seasonal_persistence";
  threshold: number;
  current_value: number;
  will_breach: boolean;
  /** True when the metric is already at/above threshold right now. */
  already_breached: boolean;
  /** Hours until the projected crossing, or null when not projected to
   * cross within the served 7-day horizon. 0 when already_breached. */
  eta_hours: number | null;
  eta_days: number | null;
  crossing_timestamp: string | null;
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
  details?: { source_ips?: string[] | null; triggered_by?: string | null } | null;
  started_at: string; // ISO 8601
  resolved_at: string | null; // ISO 8601, null while still active
  resolution_type: "automatic" | "manual" | null;
  resolution_note: string | null;
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
// per-label (the graph has eight vertex labels -- Node/Service/Network/
// Subnet/Router/FloatingIP/Instance/Port -- each with its own property
// shape; see graph_db.py's module docstring) since the frontend only needs
// a handful of well-known keys (role, state, hostname, ...) off of it, read
// defensively via lib/topology.ts's helpers.

export type TopologyVertexLabel = "Node" | "Service" | "Network" | "Subnet" | "Router" | "FloatingIP" | "Instance" | "Port";

export type TopologyEdgeType = "RUNS_ON" | "SERVES" | "CONNECTS" | "HAS_PORT";

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

// --- Per-network topology diagram (see graph_db.fetch_network_topology,
// GET /api/v1/topology/networks/{id}/diagram) -- the Horizon-style shaped
// read for NetworkTopologyDiagram.tsx. Deliberately typed with a handful of
// named fields (rather than left as loose `dict`s the way TopologyVertex's
// `properties` is) since this shape only ever comes from one purpose-built
// endpoint with one fixed nesting, unlike a generic vertex's properties.

export interface TopologyDiagramInstance {
  id: string;
  name?: string;
  status?: string;
  flavor_name?: string;
  hypervisor_hostname?: string | null;
  [key: string]: unknown;
}

export interface TopologyDiagramPort {
  id: string;
  name?: string;
  status?: string;
  admin_state_up?: boolean;
  device_owner?: string;
  fixed_ip_address?: string;
  instance: TopologyDiagramInstance | null;
  [key: string]: unknown;
}

export interface TopologyDiagramSubnet {
  id: string;
  name?: string;
  cidr?: string;
  ports: TopologyDiagramPort[];
  [key: string]: unknown;
}

export interface TopologyNetworkDiagram {
  id: string;
  name?: string;
  status?: string;
  gateway_routers: Record<string, unknown>[];
  subnets: TopologyDiagramSubnet[];
  [key: string]: unknown;
}

// --- Whole-topology, Horizon-style network map (see
// graph_db.fetch_topology_map, GET /api/v1/topology/networks/topology-map)
// -- the multi-network canvas NetworkTopologyCanvas.tsx renders, as
// opposed to TopologyNetworkDiagram above's one-network-at-a-time modal.
// Reuses TopologyDiagramSubnet's port/instance nesting (same shape, same
// endpoint family) rather than redeclaring it.

export interface TopologyMapNetwork {
  id: string;
  name?: string;
  status?: string;
  // Neutron's own "provider network" flag (`router:external`, see
  // topology_sync.py) -- true sorts this network into the provider lane,
  // false/undefined into the self-service lane.
  router_external?: boolean;
  shared?: boolean;
  // Router(s) this network is the *external gateway* for -- the
  // provider-side link (drawn above the network's trunk).
  gateway_router_ids: string[];
  // Router(s) with an internal router-interface port onto one of this
  // network's subnets -- the self-service-side link (drawn below a
  // provider network's trunk, or in place of one for a network with no
  // provider side at all).
  interface_router_ids: string[];
  subnets: TopologyDiagramSubnet[];
  [key: string]: unknown;
}

export interface TopologyMapRouter {
  id: string;
  name?: string;
  status?: string;
  // The provider network this router's external gateway sits on, or null
  // for a router with no gateway configured yet.
  gateway_network_id: string | null;
  [key: string]: unknown;
}

export interface TopologyMap {
  networks: TopologyMapNetwork[];
  routers: TopologyMapRouter[];
}

export interface NetworkLatency {
  hostname: string;
  ip_address: string | null;
  port: number;
  latency_ms: number | null;
  reachable: boolean;
  error: string | null;
}

export type NetworkHealthStatus = "ok" | "degraded";

export interface NetworkHealth {
  status: NetworkHealthStatus;
  graph_available: boolean;
  routers_down: Record<string, unknown>[];
  floating_ips_orphaned: Record<string, unknown>[];
  ports_down: Record<string, unknown>[];
  latencies: NetworkLatency[];
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

// -- Quota / budget breach alerts (distinct from AnomalyFlag) --------------
//
// See services/api/app/models.py::QuotaAlert. Two unrelated kinds of "cap"
// a project can hit, always labeled explicitly rather than as one generic
// "threshold exceeded" alert:
//   - "capacity_cap": an actual OpenStack quota (Nova/Cinder `GET /limits`).
//   - "budget_cap": a configured estimated-spend ceiling (no real billing
//     system on a self-hosted cloud, so this is a chargeback estimate).

export type QuotaBreachType = "capacity_cap" | "budget_cap";
export type QuotaSeverity = "normal" | "warning" | "critical";

// Matches services/api/app/services/quota_budget_monitor.py's resource keys.
export type QuotaResource =
  | "instances"
  | "vcpus"
  | "ram_mb"
  | "floating_ips"
  | "volumes"
  | "gigabytes"
  | "estimated_cost_eur";

export interface QuotaAlert {
  project_id: string;
  project_name: string;
  breach_type: QuotaBreachType;
  resource: QuotaResource;
  used: number;
  limit: number;
  ratio: number; // used / limit
  severity: QuotaSeverity;
  message: string | null; // null while severity === "normal"
  detected_at: string; // ISO 8601
}

export interface QuotaResyncSummary {
  status: string;
  summary: {
    projects_checked: number;
    warning_count: number;
    critical_count: number;
  };
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

// -- Agent orchestrator (POST /api/v1/agents/orchestrate) ------------------
//
// The router picks exactly one specialist per question (see services/api/
// app/agents/intent_router.py) and its raw_data shape depends on which one
// answered -- these interfaces mirror what routers/agents.py's agent nodes
// actually return, used by components/CopilotAgentPanels.tsx to pick a
// renderer.

export type AgentName = "monitoring" | "prediction" | "rag" | "anomaly" | "openstack_expert" | "network" | "security";

// Same live-status shape as LiveMetrics above, just named for clarity at
// the copilot call site.
export type AgentMonitoringData = LiveMetrics;

export interface ForecastPoint {
  horizon_hours: number;
  timestamp: string;
  predicted: number;
  lower: number;
  upper: number;
  extrapolated: boolean;
}

export interface ActualPoint {
  timestamp: string;
  value: number;
}

export interface AgentPredictionData {
  hostname: string;
  metric: string;
  model_type: string;
  generated_at: string;
  n_points_used: number;
  horizon_days: number;
  max_horizon_hours: number;
  forecast: ForecastPoint[];
  actual: ActualPoint[];
}

export interface AgentRagSource {
  source_path: string;
  doc_title: string;
  score: number;
}

export interface AgentRagData {
  sources: AgentRagSource[];
}

// Anomaly agent (v0.4, services/api/app/agents/nodes/anomaly.py) -- mirrors
// its two sub-orchestration steps exactly, so the panel can render each
// piece of evidence separately before showing the merged narrative's
// confidence. `data` on the metric signal is one of two shapes depending on
// which tier supplied it (`source`), see anomaly.py's _check_metrics.
export interface AgentAnomalyFlagSignal {
  source: "anomaly_flags";
  metric_name: string;
  current_value: number;
  z_score: number;
  severity: AnomalySeverity;
  method: AnomalyMethod;
  detected_at: string | null;
  other_flagged_metrics: string[];
}

export interface AgentAnomalyLiveSignal {
  source: "live_metrics";
  cpu_percent: number;
  memory_percent: number;
  disk_percent: number;
  status: string;
  health: string;
}

export interface AgentAnomalyMetricSignal {
  has_signal: boolean;
  detail: string;
  data: AgentAnomalyFlagSignal | AgentAnomalyLiveSignal | null;
}

export interface AgentAnomalyLogEntry {
  ts: number; // unix ms
  line: string;
  service: string | null;
}

export interface AgentAnomalyLogSignal {
  has_signal: boolean;
  detail: string;
  entries: AgentAnomalyLogEntry[];
}

// v0.9 (Phase 5): fields anomaly_arbitrate (nodes/anomaly.py) adds on top
// of whichever agent's own raw_data shape won that turn's cross-agent
// arbitration -- see that function's own docstring for the ranking rule.
// Every one of AgentAnomalyData/AgentNetworkData/AgentSecurityData can
// carry these, since arbitration can pick any of the three as primary.
export interface CrossAgentFinding {
  hostname: string;
  agent: AgentName | string;
  confidence?: number;
  summary?: string;
  has_signal?: boolean;
  // v0.9 RBAC: present (with `summary`/other specifics stripped) when
  // this entry belongs to the Security agent and the current user isn't
  // an admin -- see routers/agents.py's `_redact_security_raw_data`.
  restricted?: boolean;
}

export interface CrossAgentArbitrationFields {
  // Which agent's finding this raw_data shape actually belongs to --
  // present whenever this turn went through arbitration at all (i.e. a
  // broad incident question, not a single agent answering directly).
  investigating_agent?: AgentName | string;
  // Other agent(s) that also investigated this same host, most-confident
  // first -- present only when more than one agent found something here.
  cross_agent_findings?: CrossAgentFinding[];
  // One entry per host in scope, this host's own winning theory --
  // present only when more than one host was investigated.
  multi_node_findings?: CrossAgentFinding[];
}

export interface AgentAnomalyData extends CrossAgentArbitrationFields {
  hostname: string;
  role: string;
  metric_signal: AgentAnomalyMetricSignal;
  log_signal: AgentAnomalyLogSignal;
  // Heuristic root-cause hypothesis derived from the two signals above
  // (services/api/app/agents/nodes/anomaly.py::_hypothesize_cause) --
  // always a hedged guess, never a confirmed diagnosis. Null when nothing
  // matched.
  likely_cause: string | null;
}

// OpenStack Expert agent (v0.6, services/api/app/agents/nodes/
// openstack_expert.py) -- mirrors _build_result's raw_data shape exactly, so
// the panel can render each command with its own read-only/state-changing
// label and copy button instead of relying on the markdown answer alone.
// `matched_symptom_id` is null on the graceful "nothing in the catalog
// matched" fallback (see _run_standalone) -- that case renders no panel.
export interface AgentExpertCommand {
  command: string;
  description: string;
  read_only: boolean;
}

export type AgentExpertCategory =
  | "compute"
  | "storage"
  | "network"
  | "identity"
  | "image"
  | "message-bus"
  | "database"
  | "hypervisor"
  | "host";

export interface AgentExpertData {
  matched_symptom_id: string | null;
  matched_symptom_title?: string;
  category?: AgentExpertCategory;
  confirm_commands?: AgentExpertCommand[];
  remediation_commands?: AgentExpertCommand[];
  doc_ref?: string;
  // Present only in chained mode (see openstack_expert.py::_run_chained) --
  // which upstream agent's finding this walkthrough is explaining, and that
  // agent's own original summary, kept rather than discarded.
  diagnosed_by?: "anomaly" | "monitoring" | "network" | null;
  upstream_summary?: string;
}

// Network agent (v0.9/v0.10, services/api/app/agents/nodes/network.py) --
// mirrors _run_node_scope/_run_entity_scope's raw_data exactly. `scope`
// picks which shape is present: "node" (a physical host -- the common
// case, node_exporter interface counters + Neutron control-plane) vs.
// "network"/"subnet"/"instance" (a Neutron/Nova entity with no node_exporter
// counters at all, see network.py's module docstring on Phase C).
export interface AgentNetworkMetrics {
  node: string;
  role: string;
  instance: string;
  network_rx_bytes: number;
  network_tx_bytes: number;
  network_errors_per_sec: number;
  network_drops_per_sec: number;
  status: string;
}

export interface AgentNetworkSignal {
  has_signal: boolean;
  degraded?: boolean;
  detail: string;
  data?: unknown;
}

export interface NeutronAgentStatus {
  id: string;
  binary: string;
  agent_type: string;
  host: string;
  alive: boolean;
  admin_state_up: boolean;
}

export interface NeutronRouterStatus {
  id: string;
  name: string;
  status: string;
  admin_state_up: boolean;
}

export interface NeutronInstanceStatus {
  id: string;
  name: string;
  status: string;
  hypervisor_hostname?: string;
  has_down_port: boolean;
}

export interface AgentNetworkNeutronSignal extends AgentNetworkSignal {
  data?: {
    hostname: string;
    agents: NeutronAgentStatus[];
    routers: NeutronRouterStatus[];
    networks: unknown[];
    floating_ips: unknown[];
    instances: NeutronInstanceStatus[];
  };
  down_agents?: NeutronAgentStatus[];
  bad_routers?: NeutronRouterStatus[];
  bad_networks?: unknown[];
  bad_fips?: unknown[];
  bad_instances?: NeutronInstanceStatus[];
}

export interface AgentNetworkData extends CrossAgentArbitrationFields {
  scope: "node" | "network" | "subnet" | "instance";
  // scope === "node"
  hostname?: string;
  role?: string;
  metric_signal?: AgentNetworkSignal & { data?: AgentNetworkMetrics | null };
  neutron_signal?: AgentNetworkNeutronSignal;
  // scope !== "node" (Phase C, entity-scoped)
  entity?: { kind: "network" | "subnet" | "instance"; id: string; name: string; cidr?: string | null };
  entity_signal?: AgentNetworkSignal & {
    down_ports?: unknown[];
    down_instances?: NeutronInstanceStatus[];
  };
}

// Security agent (v0.9, services/api/app/agents/nodes/security.py) --
// mirrors _investigate's raw_data exactly: four near-independent
// sub-checks, each with the same {has_signal, degraded, detail} shape
// every "signal" dict in this codebase uses, plus its own extra evidence
// field. The detail fields below are typed optional because a non-admin
// ("viewer") account receives each sub-signal collapsed to just
// {has_signal, degraded, restricted: true} -- see routers/agents.py's
// `_redact_security_raw_data` -- not because the backend ever omits them
// for an admin caller.
export interface AgentSecuritySignal {
  has_signal: boolean;
  degraded?: boolean;
  detail?: string;
  restricted?: boolean;
}

export interface AgentAuthAnomalySignal extends AgentSecuritySignal {
  entries?: { ts: number; line: string; service: string | null }[];
}

export interface AgentSecGroupRule {
  security_group: string;
  reason: string;
  rule: {
    direction: string;
    protocol: string | null;
    port_range_min: number | null;
    port_range_max: number | null;
    remote_ip_prefix: string | null;
  };
}

export interface AgentSecGroupSignal extends AgentSecuritySignal {
  risky_rules?: AgentSecGroupRule[];
}

export interface AgentCveMatch {
  cve_id: string;
  severity: "critical" | "high" | "medium" | "low" | string;
  package: string;
  installed_version: string;
  fixed_version: string;
  description: string;
}

export interface AgentCveSignal extends AgentSecuritySignal {
  matches?: AgentCveMatch[];
}

export interface AgentEbpfAlert {
  rule: string;
  priority: string;
  output: string;
  time: string;
}

export interface AgentEbpfSignal extends AgentSecuritySignal {
  alerts?: AgentEbpfAlert[];
}

export interface AgentSecurityData extends CrossAgentArbitrationFields {
  hostname: string;
  role: string;
  has_signal: boolean;
  auth_signal: AgentAuthAnomalySignal;
  sec_group_signal: AgentSecGroupSignal;
  cve_signal: AgentCveSignal;
  ebpf_signal: AgentEbpfSignal;
}

export type AgentRawData =
  | AgentMonitoringData
  | AgentPredictionData
  | AgentRagData
  | AgentAnomalyData
  | AgentExpertData
  | AgentNetworkData
  | AgentSecurityData
  | Record<string, unknown>;

// v0.7 (adr-0009) trace step -- one per node the graph actually visited
// this turn, in execution order. `detail` is the same small, JSON-safe
// blob agents/trace.py's _safe_detail builds server-side: intent/
// target_agent on "router", critic_verdict on "critic", and otherwise
// confidence + summary (AgentResult's own plain-language narration, not a
// second description invented client-side) plus, only on
// "openstack_expert", chained_from -- which upstream agent's finding (if
// any) triggered this walkthrough, so the UI can render "network agent
// found X -> chained into openstack_expert" as one connected story
// instead of two unrelated-looking steps.
export interface AgentTraceStep {
  node: string;
  status: "ok" | "error" | "skipped" | string;
  duration_ms: number;
  timestamp: string;
  detail: {
    intent?: string | null;
    target_agent?: string | null;
    critic_verdict?: { status: string; [key: string]: unknown } | string | null;
    confidence?: number | null;
    summary?: string | null;
    chained_from?: AgentName | string | null;
    error?: string | null;
    [key: string]: unknown;
  };
}

export interface AgentOrchestrateResponse {
  answer: string;
  agent_used: AgentName | string;
  raw_data: AgentRawData | null;
  confidence: number | null;
  degraded?: boolean;
  trace_id?: string;
  critic_verdict?: string | null;
  // v0.11 (agentic-ai-layer UI): the real router -> agent [-> chained
  // agent] -> critic -> compose pipeline for this turn, inlined so the UI
  // that just triggered it can render it live -- see AgentTraceStep above.
  steps?: AgentTraceStep[];
}

