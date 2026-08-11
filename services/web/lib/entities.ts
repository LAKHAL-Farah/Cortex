/**
 * Feature 2.1 follow-up: Services + Networks list pages.
 *
 * Both pages are read entirely off the same graph the Topology page already
 * fetches (`GET /api/topology` -> `graph_db.fetch_graph()`), rather than the
 * narrower `TopologyServiceOut`/`TopologyNetworkOut` shapes
 * (`/api/v1/topology/services` / `/networks`). The full graph carries every
 * RUNS_ON/SERVES/CONNECTS edge -- including the ones those two endpoints
 * don't nest (an L3 agent SERVES a Router, a FloatingIP CONNECTS to the
 * Router it's associated with) -- so deriving rows from it client-side is
 * the only way to show *all* of a Service's or a Network entity's
 * relationships without adding new API surface. See graph_db.py's module
 * docstring and topology_sync.py for the property/edge shapes relied on
 * below.
 */
import { Cpu, HardDrive, Network as NetworkIcon, type LucideIcon } from "lucide-react";
import type { TopologyEdge, TopologyGraph, TopologyVertex, TopologyVertexLabel } from "./types";
import { vertexDisplayName } from "./topology";

// ---------------------------------------------------------------------------
// Shared indices
// ---------------------------------------------------------------------------

export interface GraphIndex {
  byId: Map<string, TopologyVertex>;
  outgoing: Map<string, TopologyEdge[]>; // keyed by source id
  incoming: Map<string, TopologyEdge[]>; // keyed by target id
}

/** One pass over the graph, reused by every derive* function below instead
 * of each doing its own O(nodes * edges) scan. */
export function buildGraphIndex(graph: TopologyGraph): GraphIndex {
  const byId = new Map<string, TopologyVertex>();
  for (const v of graph.nodes) byId.set(v.id, v);

  const outgoing = new Map<string, TopologyEdge[]>();
  const incoming = new Map<string, TopologyEdge[]>();
  for (const e of graph.edges) {
    if (!outgoing.has(e.source)) outgoing.set(e.source, []);
    outgoing.get(e.source)!.push(e);
    if (!incoming.has(e.target)) incoming.set(e.target, []);
    incoming.get(e.target)!.push(e);
  }
  return { byId, outgoing, incoming };
}

export interface VertexRef {
  id: string;
  label: TopologyVertexLabel | null;
  name: string;
}

function ref(index: GraphIndex, id: string | undefined | null): VertexRef | null {
  if (!id) return null;
  const v = index.byId.get(id);
  return { id, label: v?.label ?? null, name: v ? vertexDisplayName(v) : id };
}

function refsOf(index: GraphIndex, vertexId: string, type: TopologyEdge["type"], direction: "outgoing" | "incoming"): VertexRef[] {
  const edges = (direction === "outgoing" ? index.outgoing.get(vertexId) : index.incoming.get(vertexId)) ?? [];
  return edges
    .filter((e) => e.type === type)
    .map((e) => ref(index, direction === "outgoing" ? e.target : e.source))
    .filter((r): r is VertexRef => r !== null);
}

// ---------------------------------------------------------------------------
// Services (see topology_sync.py's tagged_services loop for the property
// shape -- id/binary/host/backend/source/zone/status/openstack_state/state)
// ---------------------------------------------------------------------------

export type ServiceSource = "nova" | "cinder" | "neutron";
export type ServiceState = "up" | "down" | "unreachable";

export interface ServiceRow {
  id: string;
  binary: string | null;
  host: string | null;
  backend: string | null;
  source: ServiceSource | string | null;
  zone: string | null;
  status: string | null; // "enabled" | "disabled" (neutron) or Nova/Cinder's own raw status
  openstackState: string | null; // Phase 2/3's raw up/down report
  state: string | null; // Phase 4's Prometheus-reconciled up/down/unreachable
  lastSyncedAt: string | null;
  hostNode: VertexRef | null; // RUNS_ON target
  serves: VertexRef[]; // SERVES targets (Network for DHCP agents, Router for L3 agents)
  properties: Record<string, unknown>;
}

export function deriveServiceRows(graph: TopologyGraph, index: GraphIndex): ServiceRow[] {
  return graph.nodes
    .filter((v) => v.label === "Service")
    .map((v) => {
      const p = v.properties;
      const hostNode = refsOf(index, v.id, "RUNS_ON", "outgoing")[0] ?? null;
      return {
        id: v.id,
        binary: (p.binary as string) ?? null,
        host: (p.host as string) ?? null,
        backend: (p.backend as string) ?? null,
        source: (p.source as string) ?? null,
        zone: (p.zone as string) ?? null,
        status: (p.status as string) ?? null,
        openstackState: (p.openstack_state as string) ?? null,
        state: (p.state as string) ?? null,
        lastSyncedAt: (p.last_synced_at as string) ?? null,
        hostNode,
        serves: refsOf(index, v.id, "SERVES", "outgoing"),
        properties: p,
      };
    })
    .sort((a, b) => (a.binary ?? a.id).localeCompare(b.binary ?? b.id) || (a.host ?? "").localeCompare(b.host ?? ""));
}

export const SERVICE_STATE_COLOR: Record<string, string> = {
  up: "var(--ok)",
  down: "var(--crit)",
  unreachable: "var(--warn)",
};
export const SERVICE_STATE_SOFT: Record<string, string> = {
  up: "var(--ok-soft)",
  down: "var(--crit-soft)",
  unreachable: "var(--warn-soft)",
};
export const SERVICE_STATE_LABEL: Record<string, string> = {
  up: "Up",
  down: "Down",
  unreachable: "Unreachable",
};

export const SERVICE_SOURCE_LABEL: Record<string, string> = {
  nova: "Nova",
  cinder: "Cinder",
  neutron: "Neutron",
};

// One color + one lucide icon per OpenStack project a Service can come
// from, so the card/table "asset" badge reads as compute (Nova) vs. block
// storage (Cinder) vs. networking (Neutron) at a glance instead of every
// Service sharing the same generic Boxes glyph. Cinder reuses
// --role-storage so it lines up with the same green used for storage :Node
// roles elsewhere (NodeCard.tsx).
export const SERVICE_SOURCE_COLOR: Record<string, string> = {
  nova: "var(--chart-1)",
  cinder: "var(--role-storage)",
  neutron: "var(--chart-2)",
};

export const SERVICE_SOURCE_ICON: Record<string, LucideIcon> = {
  nova: Cpu,
  cinder: HardDrive,
  neutron: NetworkIcon,
};

// ---------------------------------------------------------------------------
// "Hosted on" / other free-text tags: a small, fixed color palette hashed
// off the tag's own text so the same host always renders the same color
// (no per-host config needed) while still reading as a proper Notion-style
// color-coded tag instead of a plain link.
// ---------------------------------------------------------------------------

const TAG_PALETTE = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--role-controller)",
  "var(--role-monitoring)",
] as const;

export function tagColorForKey(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i++) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  return TAG_PALETTE[Math.abs(hash) % TAG_PALETTE.length];
}

// ---------------------------------------------------------------------------
// Networks / Subnets / Routers / FloatingIPs (see topology_sync.py's
// _sync_networks_to_graph / _sync_subnets_to_graph / _sync_routers_to_graph /
// _sync_router_gateways_to_graph / _sync_floating_ips_to_graph /
// _sync_floating_ip_routers_to_graph / _sync_dhcp_hosting_to_graph /
// _sync_l3_hosting_to_graph for exactly which edges connect these)
// ---------------------------------------------------------------------------

export interface NetworkRow {
  id: string;
  name: string | null;
  status: string | null;
  adminStateUp: boolean | null;
  shared: boolean | null;
  projectId: string | null;
  lastSyncedAt: string | null;
  subnets: VertexRef[]; // (:Subnet)-[:CONNECTS]->(:Network)
  gatewayRouters: VertexRef[]; // (:Router)-[:CONNECTS]->(:Network)
  floatingIps: VertexRef[]; // (:FloatingIP)-[:CONNECTS]->(:Network)
  servingAgents: VertexRef[]; // (:Service)-[:SERVES]->(:Network), i.e. DHCP agents
  properties: Record<string, unknown>;
}

export interface SubnetRow {
  id: string;
  name: string | null;
  cidr: string | null;
  ipVersion: number | null;
  gatewayIp: string | null;
  lastSyncedAt: string | null;
  network: VertexRef | null; // (:Subnet)-[:CONNECTS]->(:Network)
  properties: Record<string, unknown>;
}

export interface RouterRow {
  id: string;
  name: string | null;
  status: string | null;
  adminStateUp: boolean | null;
  projectId: string | null;
  lastSyncedAt: string | null;
  gatewayNetwork: VertexRef | null; // (:Router)-[:CONNECTS]->(:Network), only if gatewayed
  floatingIps: VertexRef[]; // (:FloatingIP)-[:CONNECTS]->(:Router)
  servingAgents: VertexRef[]; // (:Service)-[:SERVES]->(:Router), i.e. L3 agents
  properties: Record<string, unknown>;
}

export interface FloatingIpRow {
  id: string;
  floatingIpAddress: string | null;
  fixedIpAddress: string | null;
  status: string | null;
  lastSyncedAt: string | null;
  network: VertexRef | null; // (:FloatingIP)-[:CONNECTS]->(:Network), always present
  router: VertexRef | null; // (:FloatingIP)-[:CONNECTS]->(:Router), only if associated
  properties: Record<string, unknown>;
}

export function deriveNetworkRows(graph: TopologyGraph, index: GraphIndex): NetworkRow[] {
  return graph.nodes
    .filter((v) => v.label === "Network")
    .map((v) => {
      const p = v.properties;
      return {
        id: v.id,
        name: (p.name as string) ?? null,
        status: (p.status as string) ?? null,
        adminStateUp: (p.admin_state_up as boolean) ?? null,
        shared: (p.shared as boolean) ?? null,
        projectId: (p.project_id as string) ?? null,
        lastSyncedAt: (p.last_synced_at as string) ?? null,
        subnets: refsOf(index, v.id, "CONNECTS", "incoming").filter((r) => r.label === "Subnet"),
        gatewayRouters: refsOf(index, v.id, "CONNECTS", "incoming").filter((r) => r.label === "Router"),
        floatingIps: refsOf(index, v.id, "CONNECTS", "incoming").filter((r) => r.label === "FloatingIP"),
        servingAgents: refsOf(index, v.id, "SERVES", "incoming"),
        properties: p,
      };
    })
    .sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id));
}

export function deriveSubnetRows(graph: TopologyGraph, index: GraphIndex): SubnetRow[] {
  return graph.nodes
    .filter((v) => v.label === "Subnet")
    .map((v) => {
      const p = v.properties;
      return {
        id: v.id,
        name: (p.name as string) ?? null,
        cidr: (p.cidr as string) ?? null,
        ipVersion: (p.ip_version as number) ?? null,
        gatewayIp: (p.gateway_ip as string) ?? null,
        lastSyncedAt: (p.last_synced_at as string) ?? null,
        network: refsOf(index, v.id, "CONNECTS", "outgoing")[0] ?? null,
        properties: p,
      };
    })
    .sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id));
}

export function deriveRouterRows(graph: TopologyGraph, index: GraphIndex): RouterRow[] {
  return graph.nodes
    .filter((v) => v.label === "Router")
    .map((v) => {
      const p = v.properties;
      return {
        id: v.id,
        name: (p.name as string) ?? null,
        status: (p.status as string) ?? null,
        adminStateUp: (p.admin_state_up as boolean) ?? null,
        projectId: (p.project_id as string) ?? null,
        lastSyncedAt: (p.last_synced_at as string) ?? null,
        gatewayNetwork: refsOf(index, v.id, "CONNECTS", "outgoing").find((r) => r.label === "Network") ?? null,
        floatingIps: refsOf(index, v.id, "CONNECTS", "incoming").filter((r) => r.label === "FloatingIP"),
        servingAgents: refsOf(index, v.id, "SERVES", "incoming"),
        properties: p,
      };
    })
    .sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id));
}

export function deriveFloatingIpRows(graph: TopologyGraph, index: GraphIndex): FloatingIpRow[] {
  return graph.nodes
    .filter((v) => v.label === "FloatingIP")
    .map((v) => {
      const p = v.properties;
      const connects = refsOf(index, v.id, "CONNECTS", "outgoing");
      return {
        id: v.id,
        floatingIpAddress: (p.floating_ip_address as string) ?? null,
        fixedIpAddress: (p.fixed_ip_address as string) ?? null,
        status: (p.status as string) ?? null,
        lastSyncedAt: (p.last_synced_at as string) ?? null,
        network: connects.find((r) => r.label === "Network") ?? null,
        router: connects.find((r) => r.label === "Router") ?? null,
        properties: p,
      };
    })
    .sort((a, b) => (a.floatingIpAddress ?? a.id).localeCompare(b.floatingIpAddress ?? b.id));
}

// Neutron's own status vocabulary (ACTIVE/DOWN/BUILD/ERROR), shared by
// Network/Subnet*/Router/FloatingIP -- *Subnet has no `status` of its own in
// this graph, so it never uses this map. Kept separate from
// SERVICE_STATE_COLOR above since a Service's up/down/unreachable is a
// different vocabulary that happens to reuse the same ok/crit/warn colors.
export const NEUTRON_STATUS_COLOR: Record<string, string> = {
  ACTIVE: "var(--ok)",
  DOWN: "var(--crit)",
  BUILD: "var(--warn)",
  ERROR: "var(--crit)",
};
export const NEUTRON_STATUS_SOFT: Record<string, string> = {
  ACTIVE: "var(--ok-soft)",
  DOWN: "var(--crit-soft)",
  BUILD: "var(--warn-soft)",
  ERROR: "var(--crit-soft)",
};

// ---------------------------------------------------------------------------
// /networks chooser page: URL-friendly slug <-> graph vertex label, for the
// dynamic /networks/[type] route. Kept here (not inline in the page) so
// NetworksOverview.tsx and app/networks/[type]/page.tsx share one mapping
// instead of two copies drifting apart.
// ---------------------------------------------------------------------------

export type NetworkEntityLabel = "Network" | "Subnet" | "Router" | "FloatingIP";

export const NETWORK_ENTITY_SLUGS: Record<string, NetworkEntityLabel> = {
  networks: "Network",
  subnets: "Subnet",
  routers: "Router",
  "floating-ips": "FloatingIP",
};

export function slugForNetworkEntity(label: NetworkEntityLabel): string {
  return Object.entries(NETWORK_ENTITY_SLUGS).find(([, l]) => l === label)?.[0] ?? label.toLowerCase();
}

/** Normalized shape the Network/Subnet/Router/FloatingIP card + table share,
 * so app/networks/[type] can render all four with one pair of components
 * instead of four. `chips` are short key facts (CIDR, admin state, shared,
 * ...); `relations`/`relationLists` are the "which net does this belong to"
 * data the user asked for -- singular for a fixed one-to-one link (a
 * Subnet's Network, a FloatingIP's Network/Router), plural for a one-to-many
 * one (a Network's Subnets/Routers/FloatingIPs/serving agents). */
export interface NetworkEntityDisplayRow {
  id: string;
  label: NetworkEntityLabel;
  title: string;
  subtitle: string | null;
  status: string | null;
  chips: { label: string; value: string }[];
  relations: { label: string; ref: VertexRef | null }[];
  relationLists: { label: string; refs: VertexRef[] }[];
  lastSyncedAt: string | null;
}

export function deriveNetworkEntityDisplayRows(
  label: NetworkEntityLabel,
  graph: TopologyGraph,
  index: GraphIndex
): NetworkEntityDisplayRow[] {
  if (label === "Network") {
    return deriveNetworkRows(graph, index).map((n) => ({
      id: n.id,
      label,
      title: n.name ?? n.id,
      subtitle: n.shared ? "Shared" : n.projectId ? `project ${n.projectId}` : null,
      status: n.status,
      chips: [
        { label: "Admin state", value: n.adminStateUp === null ? "—" : n.adminStateUp ? "up" : "down" },
        { label: "Shared", value: n.shared ? "yes" : "no" },
      ],
      relations: [],
      relationLists: [
        { label: "Subnets", refs: n.subnets },
        { label: "Gateway routers", refs: n.gatewayRouters },
        { label: "Floating IPs", refs: n.floatingIps },
        { label: "DHCP agents", refs: n.servingAgents },
      ],
      lastSyncedAt: n.lastSyncedAt,
    }));
  }
  if (label === "Subnet") {
    return deriveSubnetRows(graph, index).map((s) => ({
      id: s.id,
      label,
      title: s.name ?? s.id,
      subtitle: s.cidr,
      status: null,
      chips: [
        { label: "IP version", value: s.ipVersion === null ? "—" : `v${s.ipVersion}` },
        { label: "Gateway IP", value: s.gatewayIp ?? "—" },
      ],
      relations: [{ label: "Network", ref: s.network }],
      relationLists: [],
      lastSyncedAt: s.lastSyncedAt,
    }));
  }
  if (label === "Router") {
    return deriveRouterRows(graph, index).map((r) => ({
      id: r.id,
      label,
      title: r.name ?? r.id,
      subtitle: r.projectId ? `project ${r.projectId}` : null,
      status: r.status,
      chips: [{ label: "Admin state", value: r.adminStateUp === null ? "—" : r.adminStateUp ? "up" : "down" }],
      relations: [{ label: "Gateway network", ref: r.gatewayNetwork }],
      relationLists: [
        { label: "Floating IPs", refs: r.floatingIps },
        { label: "L3 agents", refs: r.servingAgents },
      ],
      lastSyncedAt: r.lastSyncedAt,
    }));
  }
  // FloatingIP
  return deriveFloatingIpRows(graph, index).map((f) => ({
    id: f.id,
    label,
    title: f.floatingIpAddress ?? f.id,
    subtitle: f.fixedIpAddress ? `-> ${f.fixedIpAddress}` : "unassociated",
    status: f.status,
    chips: [],
    relations: [
      { label: "Network", ref: f.network },
      { label: "Router", ref: f.router },
    ],
    relationLists: [],
    lastSyncedAt: f.lastSyncedAt,
  }));
}
