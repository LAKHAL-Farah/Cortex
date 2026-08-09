import type {
  NodeRole,
  TopologyEdgeType,
  TopologySyncStatus,
  TopologyVertex,
  TopologyVertexLabel,
} from "./types";

/** Re-exported so topology components format "synced Xm ago" the same way
 * Alerts/Baselines already do -- see lib/anomalies.ts::formatRelative. */
export { formatRelative } from "./anomalies";

// Node vertices carry the same `role` values as the Postgres-backed node
// registry (see topology_sync.py's `graph_node["role"] = pg_node.role`),
// so reuse NodeCard.tsx's exact palette rather than inventing a second one.
export const ROLE_COLOR: Record<NodeRole, string> = {
  controller: "var(--role-controller)",
  compute: "var(--role-compute)",
  storage: "var(--role-storage)",
  monitoring: "var(--role-monitoring)",
};

// Everything that isn't a :Node (Service/Network/Subnet/Router/FloatingIP)
// gets one color per label instead, off the existing chart palette so it
// stays visually distinct from the role colors above without adding new
// theme variables.
export const LABEL_COLOR: Record<Exclude<TopologyVertexLabel, "Node">, string> = {
  Service: "var(--chart-2)",
  Network: "var(--chart-3)",
  Subnet: "var(--chart-4)",
  Router: "var(--chart-5)",
  FloatingIP: "var(--medium)",
};

/** Single color for a vertex: role color for :Node (falling back to accent
 * for the rare node with no role yet -- see topology_sync.py), label color
 * for everything else. */
export function vertexColor(vertex: Pick<TopologyVertex, "label" | "properties">): string {
  if (vertex.label === "Node") {
    const role = vertex.properties.role as NodeRole | null | undefined;
    return (role && ROLE_COLOR[role]) || "var(--accent)";
  }
  return LABEL_COLOR[vertex.label as Exclude<TopologyVertexLabel, "Node">] ?? "var(--text-muted)";
}

/** Short display label for a vertex: hostname/name property when the
 * graph has one (Node.hostname, Network/Router/Subnet.name), otherwise the
 * binary (Service) or the raw id, so nothing renders blank on the graph. */
export function vertexDisplayName(vertex: Pick<TopologyVertex, "id" | "properties">): string {
  const p = vertex.properties;
  return (
    (p.hostname as string | undefined) ||
    (p.name as string | undefined) ||
    (p.binary as string | undefined) ||
    (p.floating_ip_address as string | undefined) ||
    vertex.id
  );
}

// Dash pattern per relationship type, so the three edge kinds (structural
// RUNS_ON/CONNECTS vs. the more dynamic SERVES) read apart at a glance
// without relying on color alone -- see graph_db.py's module docstring for
// what each type means.
export const EDGE_DASH: Record<TopologyEdgeType, number[]> = {
  RUNS_ON: [],
  CONNECTS: [],
  SERVES: [4, 3],
};

export const EDGE_LABEL: Record<TopologyEdgeType, string> = {
  RUNS_ON: "runs on",
  CONNECTS: "connects",
  SERVES: "serves",
};

// Mirrors routers/topology.py's _STATUS_SEVERITY ordering, for coloring the
// staleness/health badge consistently with what the API considers "worse".
export const SYNC_STATUS_COLOR: Record<TopologySyncStatus, string> = {
  ok: "var(--ok)",
  unknown: "var(--text-muted)",
  degraded: "var(--warn)",
  failed: "var(--crit)",
};

export const SYNC_STATUS_SOFT: Record<TopologySyncStatus, string> = {
  ok: "var(--ok-soft)",
  unknown: "var(--canvas)",
  degraded: "var(--warn-soft)",
  failed: "var(--crit-soft)",
};

export const SYNC_STATUS_LABEL: Record<TopologySyncStatus, string> = {
  ok: "Synced",
  unknown: "Never synced",
  degraded: "Degraded",
  failed: "Failed",
};
