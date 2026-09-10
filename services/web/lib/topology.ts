import {
  Activity,
  Boxes,
  Cpu,
  Globe,
  Grid2x2,
  HardDrive,
  Monitor,
  Network as NetworkIcon,
  Plug,
  Router as RouterIcon,
  Server,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
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
  // chart-1 was unclaimed here (Node uses role colors, not this map) --
  // Instance is the vertex type a person most wants to spot at a glance
  // (their actual workload), so it gets a real, distinct color rather
  // than a muted afterthought.
  Instance: "var(--chart-1)",
  // chart-6 added specifically for this -- see globals.css's comment on
  // why reusing an existing color would collide with what it already
  // means elsewhere on this same graph.
  Port: "var(--chart-6)",
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

// Per-role glyph drawn *inside* the hexagon on the canvas graph (see
// TopologyGraph.tsx's drawGlyph) -- kept as plain string keys rather than
// importing lucide's React components here, since the canvas paint path
// draws its own tiny vector glyphs instead of rasterizing SVGs every
// frame. NODE_GLYPH/LABEL_GLYPH below are the source of truth for *which*
// glyph a vertex gets; VERTEX_ICON (further down) is the lucide-react
// equivalent used everywhere the icon is rendered as real DOM (legend,
// hover tooltip, detail panel) instead of painted on a <canvas>.
export type VertexGlyph = "shield" | "cpu" | "disk" | "pulse" | "server" | "box" | "share" | "grid" | "router" | "globe" | "vm" | "plug";

const NODE_ROLE_GLYPH: Record<NodeRole, VertexGlyph> = {
  controller: "shield",
  compute: "cpu",
  storage: "disk",
  monitoring: "pulse",
};

const LABEL_GLYPH: Record<Exclude<TopologyVertexLabel, "Node">, VertexGlyph> = {
  Service: "box",
  Network: "share",
  Subnet: "grid",
  Router: "router",
  FloatingIP: "globe",
  Instance: "vm",
  Port: "plug",
};

/** Which glyph a vertex gets on the canvas graph -- role-specific for
 * :Node, one per label otherwise. Mirrors vertexColor()'s branching. */
export function vertexGlyph(vertex: Pick<TopologyVertex, "label" | "properties">): VertexGlyph {
  if (vertex.label === "Node") {
    const role = vertex.properties.role as NodeRole | null | undefined;
    return (role && NODE_ROLE_GLYPH[role]) || "server";
  }
  return LABEL_GLYPH[vertex.label as Exclude<TopologyVertexLabel, "Node">] ?? "server";
}

// lucide-react equivalent of the glyphs above, for the spots this feature
// renders an icon as normal DOM (search/filter chips, legend, hover
// tooltip, detail panel header) rather than painting it into the graph
// canvas.
const NODE_ROLE_ICON: Record<NodeRole, LucideIcon> = {
  controller: ShieldCheck,
  compute: Cpu,
  storage: HardDrive,
  monitoring: Activity,
};

const LABEL_ICON: Record<Exclude<TopologyVertexLabel, "Node">, LucideIcon> = {
  Service: Boxes,
  Network: NetworkIcon,
  Subnet: Grid2x2,
  Router: RouterIcon,
  FloatingIP: Globe,
  Instance: Monitor,
  Port: Plug,
};

export function vertexIcon(vertex: Pick<TopologyVertex, "label" | "properties">): LucideIcon {
  if (vertex.label === "Node") {
    const role = vertex.properties.role as NodeRole | null | undefined;
    return (role && NODE_ROLE_ICON[role]) || Server;
  }
  return LABEL_ICON[vertex.label as Exclude<TopologyVertexLabel, "Node">] ?? Server;
}

/** Best-effort one-word status straight off the raw sync properties (see
 * topology_sync.py: Service carries `status`/`state`, Network/Subnet/
 * Router/FloatingIP carry `status`, Node carries `hypervisor_status`).
 * Returns null when the vertex type doesn't carry one, rather than
 * guessing -- callers should just omit the row. */
export function vertexStatusText(vertex: Pick<TopologyVertex, "properties">): string | null {
  const p = vertex.properties;
  const raw =
    (p.status as string | null | undefined) ??
    (p.state as string | null | undefined) ??
    (p.hypervisor_status as string | null | undefined) ??
    (p.hypervisor_state as string | null | undefined);
  return raw ? String(raw) : null;
}

// Base radius (px) a vertex is painted at before hover/select scaling (see
// TopologyGraph.tsx's paintNode) -- sized by how load-bearing that vertex
// type is to *understanding* the topology, not by how many of them exist.
// A hypervisor Node anchors everything hosted on it and is what a person
// is most often searching for, so it's the largest shape on the graph; a
// Port is the most numerous, most leaf-like vertex (a VM's network
// attachment point, not a resource in its own right) and reads fine
// small. Network/Router (the two things everything else attaches to)
// come next, then Subnet, then Service/Instance (individually important
// but numerous), then FloatingIP/Port (leaf-like, numerous).
export const VERTEX_RADIUS: Record<TopologyVertexLabel, number> = {
  Node: 9,
  Network: 7.5,
  Router: 7.5,
  Subnet: 7,
  Service: 6,
  Instance: 6,
  FloatingIP: 5,
  Port: 5,
};

export function vertexRadius(vertex: Pick<TopologyVertex, "label">): number {
  return VERTEX_RADIUS[vertex.label] ?? 6;
}

// Dash pattern per relationship type, so the four edge kinds (structural
// RUNS_ON/CONNECTS/HAS_PORT vs. the more dynamic SERVES) read apart at a
// glance without relying on color alone -- see graph_db.py's module
// docstring for what each type means.
export const EDGE_DASH: Record<TopologyEdgeType, number[]> = {
  RUNS_ON: [],
  CONNECTS: [2, 2],
  HAS_PORT: [1, 1],
  SERVES: [5, 3],
};

// Color per relationship type -- off the same restrained chart/status
// palette as everything else in the app (see globals.css), not a fourth
// arbitrary color scale. RUNS_ON (hosting) reads as neutral/structural,
// CONNECTS/HAS_PORT (network topology) take the Port/Instance colors
// they connect (HAS_PORT specifically reuses Port's own chart-6, since
// it's the edge a VM's Port hangs off), SERVES (live service traffic)
// takes the accent so the one relationship type that's actually about
// running traffic is the one that pops.
export const EDGE_COLOR: Record<TopologyEdgeType, string> = {
  RUNS_ON: "var(--text-muted)",
  CONNECTS: "var(--chart-3)",
  HAS_PORT: "var(--chart-6)",
  SERVES: "var(--accent)",
};

export const EDGE_LABEL: Record<TopologyEdgeType, string> = {
  RUNS_ON: "runs on",
  CONNECTS: "connects",
  HAS_PORT: "has port",
  SERVES: "serves",
};

// Only SERVES gets an animated directional particle (see
// TopologyGraph.tsx's linkDirectionalParticles) -- it's the one
// relationship that represents live traffic (an OpenStack agent serving a
// network); RUNS_ON/CONNECTS/HAS_PORT are structural and stay static so
// the graph doesn't turn into a wall of moving dots.
export const EDGE_PARTICLES: Record<TopologyEdgeType, number> = {
  RUNS_ON: 0,
  CONNECTS: 0,
  HAS_PORT: 0,
  SERVES: 2,
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

// Fixed display order for the label filter chips / legend, so they don't
// jump around between renders (Object.entries on the graph response isn't
// order-stable across syncs).
export const VERTEX_LABELS: TopologyVertexLabel[] = ["Node", "Service", "Network", "Subnet", "Router", "FloatingIP", "Instance", "Port"];

/** Does this vertex match a free-text search? Checks the id, display name,
 * and (for :Node) role, so "compute" or a partial hostname both work. */
export function vertexMatchesQuery(vertex: TopologyVertex, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const role = (vertex.properties.role as string | undefined) ?? "";
  return (
    vertexDisplayName(vertex).toLowerCase().includes(q) ||
    vertex.id.toLowerCase().includes(q) ||
    vertex.label.toLowerCase().includes(q) ||
    role.toLowerCase().includes(q)
  );
}
