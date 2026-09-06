"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { AnimatePresence } from "framer-motion";
import {
  AlertTriangle,
  Globe,
  Grid2x2,
  Monitor,
  Network as NetworkIcon,
  Plug,
  Router as RouterIcon,
  Search,
  ShieldCheck,
} from "lucide-react";
import type {
  TopologyDiagramPort,
  TopologyDiagramSubnet,
  TopologyMap,
  TopologyMapNetwork,
  TopologyMapRouter,
} from "@/lib/types";
import { NEUTRON_STATUS_COLOR, NEUTRON_STATUS_SOFT } from "@/lib/entities";
import { Card } from "./ui/Card";
import NetworkTopologyDiagram from "./NetworkTopologyDiagram";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

// Same restrained chart palette every other topology visual in this app
// draws from (see lib/topology.ts's LABEL_COLOR) -- cycled by network here
// since each trunk needs its own identity color, not a per-vertex-label
// one. Assigned by sorted network id (below) so a given network keeps the
// same color across renders/refreshes instead of jumping around.
const TRUNK_PALETTE = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--medium)",
];

// Same "is this port worth flagging" rule NetworkTopologyDiagram.tsx's
// PortChip already applies one network at a time -- repeated here so a
// whole network's trunk (and the summary bar's issue count) can roll it up
// across every subnet at once.
function portHasIssue(port: TopologyDiagramPort): boolean {
  return port.instance !== null && (port.status !== "ACTIVE" || port.admin_state_up === false);
}

function networkIssueCount(net: TopologyMapNetwork): number {
  return net.subnets.reduce((n, sub) => n + sub.ports.filter(portHasIssue).length, 0);
}

function networkInstanceCount(net: TopologyMapNetwork): number {
  return net.subnets.reduce((n, sub) => n + sub.ports.filter((p) => p.instance !== null).length, 0);
}

function matchesQuery(net: TopologyMapNetwork, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (net.name ?? net.id).toLowerCase().includes(q) || net.id.toLowerCase().includes(q);
}

/** One port, compact enough to stack many of them in a narrow trunk
 * column -- same status vocabulary/coloring as PortChip in
 * NetworkTopologyDiagram.tsx, just a single row instead of a card. */
function PortRow({ port }: { port: TopologyDiagramPort }) {
  const instance = port.instance;
  const status = instance?.status ?? port.status ?? null;
  const color = (status && NEUTRON_STATUS_COLOR[status]) || "var(--text-muted)";
  const soft = (status && NEUTRON_STATUS_SOFT[status]) || "var(--canvas)";
  const issue = portHasIssue(port);
  const Icon = instance ? Monitor : Plug;
  const label = instance ? instance.name ?? instance.id : port.name ?? port.id;
  const subtitle = instance ? port.fixed_ip_address : port.device_owner ?? "system port";

  return (
    <div
      className="flex items-center gap-1.5 rounded-[var(--radius-control)] border px-2 py-1.5"
      style={{
        borderColor: issue ? "var(--crit)" : "var(--border-soft)",
        background: instance ? soft : "var(--canvas)",
      }}
      title={`${label}${subtitle ? ` · ${subtitle}` : ""}`}
    >
      <Icon className="h-3 w-3 flex-shrink-0" style={{ color: instance ? color : "var(--text-muted)" }} strokeWidth={2} />
      <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-color-text">{label}</span>
      {issue ? (
        <AlertTriangle className="h-3 w-3 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2.5} />
      ) : (
        status && <span className="status-dot flex-shrink-0" style={{ background: color }} />
      )}
    </div>
  );
}

function SubnetBlock({ subnet, trunkColor }: { subnet: TopologyDiagramSubnet; trunkColor: string }) {
  return (
    <div
      className="rounded-[var(--radius-control)] border p-2"
      style={{ borderColor: "var(--border-soft)", background: "var(--surface)" }}
    >
      <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold" style={{ color: trunkColor }}>
        <Grid2x2 className="h-3 w-3 flex-shrink-0" strokeWidth={2} />
        <span className="truncate">{subnet.name ?? subnet.id}</span>
      </div>
      {subnet.cidr && <div className="mb-1.5 text-[10px] text-text-faint">{subnet.cidr}</div>}
      {subnet.ports.length === 0 ? (
        <div className="text-[10px] text-text-faint">No ports</div>
      ) : (
        <div className="flex flex-col gap-1">
          {subnet.ports.map((port) => (
            <PortRow key={port.id} port={port} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Sits above a self-service network's trunk, capping it the way Horizon
 * draws a router between a tenant network and the provider network it
 * gateways out through -- see graph_db.fetch_topology_map's
 * `interface_router_ids` docstring for where this link comes from. */
function RouterCap({ router, gatewayNetworkName }: { router: TopologyMapRouter; gatewayNetworkName: string | null }) {
  return (
    <div className="flex flex-col items-center gap-1 pb-1">
      <div
        className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium"
        style={{
          borderColor: "var(--chart-5)",
          color: "var(--chart-5)",
          background: "color-mix(in srgb, var(--chart-5) 10%, transparent)",
        }}
        title={router.status ? `${router.name ?? router.id} · ${router.status}` : router.name ?? router.id}
      >
        <RouterIcon className="h-3 w-3 flex-shrink-0" strokeWidth={2} />
        <span className="max-w-[140px] truncate">{router.name ?? router.id}</span>
      </div>
      <div className="text-[10px] text-text-faint">{gatewayNetworkName ? `→ ${gatewayNetworkName}` : "no external gateway"}</div>
      <div className="h-4 w-px" style={{ background: "var(--border)" }} aria-hidden="true" />
    </div>
  );
}

function NetworkColumn({
  network,
  color,
  routersById,
  networksById,
  onOpenDiagram,
}: {
  network: TopologyMapNetwork;
  color: string;
  routersById: Map<string, TopologyMapRouter>;
  networksById: Map<string, TopologyMapNetwork>;
  onOpenDiagram: (id: string) => void;
}) {
  const issueCount = networkIssueCount(network);
  const interfaceRouters = network.interface_router_ids
    .map((id) => routersById.get(id))
    .filter((r): r is TopologyMapRouter => !!r);
  const gatewayRouterCount = network.gateway_router_ids.length;

  return (
    <div className="flex w-[240px] flex-shrink-0 flex-col items-center">
      {/* Self-service side: cap the trunk with the router(s) that
          interface onto it -- most networks have exactly one; a rare
          multi-router case (HA/DVR) shows the first plus a "+N" note
          rather than stacking every one. */}
      {interfaceRouters.length > 0 && (
        <RouterCap
          router={interfaceRouters[0]}
          gatewayNetworkName={
            interfaceRouters[0].gateway_network_id
              ? networksById.get(interfaceRouters[0].gateway_network_id)?.name ??
                interfaceRouters[0].gateway_network_id
              : null
          }
        />
      )}
      {interfaceRouters.length > 1 && (
        <div className="-mt-1 mb-1 text-[10px] text-text-faint">+{interfaceRouters.length - 1} more router(s)</div>
      )}

      <div className="w-full rounded-[var(--radius-panel)] border" style={{ borderColor: "var(--border-soft)", background: "var(--canvas)" }}>
        <button
          onClick={() => onOpenDiagram(network.id)}
          className="w-full rounded-t-[var(--radius-panel)] p-3 text-left transition-colors hover:bg-[var(--surface)]"
          style={{ borderBottom: `3px solid ${color}` }}
          title="Open this network's diagram"
        >
          <div className="flex items-center gap-1.5">
            <NetworkIcon className="h-3.5 w-3.5 flex-shrink-0" style={{ color }} strokeWidth={2} />
            <span className="min-w-0 flex-1 truncate text-xs font-semibold text-color-text">{network.name ?? network.id}</span>
          </div>
          <div className="mt-1 flex items-center gap-1 text-[10px] text-text-faint">
            {network.router_external ? (
              <span className="inline-flex items-center gap-1"><Globe className="h-2.5 w-2.5" strokeWidth={2} />Provider</span>
            ) : (
              <span className="inline-flex items-center gap-1"><ShieldCheck className="h-2.5 w-2.5" strokeWidth={2} />Self-service</span>
            )}
            {network.status && <span>· {network.status}</span>}
          </div>
          {network.router_external && gatewayRouterCount > 0 && (
            <div className="mt-1 text-[10px] text-text-faint">
              Gateway for {gatewayRouterCount} router{gatewayRouterCount === 1 ? "" : "s"}
            </div>
          )}
          {issueCount > 0 && (
            <div
              className="mt-1.5 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
              style={{ color: "var(--crit)", background: "var(--crit-soft)" }}
            >
              <AlertTriangle className="h-2.5 w-2.5" strokeWidth={2.5} />
              {issueCount} issue{issueCount === 1 ? "" : "s"}
            </div>
          )}
        </button>

        <div className="flex flex-col gap-2 p-2.5">
          {network.subnets.length === 0 ? (
            <div className="py-2 text-center text-[10px] text-text-faint">No subnets</div>
          ) : (
            network.subnets.map((subnet) => <SubnetBlock key={subnet.id} subnet={subnet} trunkColor={color} />)
          )}
        </div>
      </div>
    </div>
  );
}

/** Whole-topology, Horizon-style network map: every network as its own
 * colored trunk, sorted into a "Provider networks" lane (Neutron's
 * `router:external` networks -- see topology_sync.py) and a "Self-service
 * networks" lane, with the router that gateways each self-service network
 * out capping its trunk. Reads GET /api/topology/networks/topology-map
 * (graph_db.fetch_topology_map) -- the multi-network sibling of
 * NetworkTopologyDiagram.tsx's one-network-at-a-time modal, which this
 * component still opens (via `onOpenDiagram`) for a closer look at any
 * one network. */
export default function NetworkTopologyCanvas() {
  const { data, error, isLoading } = useSWR<TopologyMap>("/api/topology/networks/topology-map", fetcher, {
    refreshInterval: 15000,
  });
  const [query, setQuery] = useState("");
  const [diagramNetworkId, setDiagramNetworkId] = useState<string | null>(null);

  const routersById = useMemo(() => new Map((data?.routers ?? []).map((r) => [r.id, r])), [data]);
  const networksById = useMemo(() => new Map((data?.networks ?? []).map((n) => [n.id, n])), [data]);

  const colorForNetwork = useMemo(() => {
    const sortedIds = [...(data?.networks ?? [])].map((n) => n.id).sort();
    const byId = new Map(sortedIds.map((id, i) => [id, TRUNK_PALETTE[i % TRUNK_PALETTE.length]]));
    return (id: string) => byId.get(id) ?? "var(--text-muted)";
  }, [data]);

  const { providerNetworks, selfServiceNetworks } = useMemo(() => {
    const visible = (data?.networks ?? [])
      .filter((n) => matchesQuery(n, query.trim()))
      .sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id));
    return {
      providerNetworks: visible.filter((n) => n.router_external),
      selfServiceNetworks: visible.filter((n) => !n.router_external),
    };
  }, [data, query]);

  const totalNetworks = data?.networks.length ?? 0;
  const totalInstances = useMemo(() => (data?.networks ?? []).reduce((n, net) => n + networkInstanceCount(net), 0), [data]);
  const totalIssues = useMemo(() => (data?.networks ?? []).reduce((n, net) => n + networkIssueCount(net), 0), [data]);

  return (
    <Card padding="p-5" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="eyebrow">Network topology map</div>
          <p className="mt-1 text-sm text-text-faint">
            Every network as its own trunk, sorted into provider (externally reachable) and self-service lanes --
            click a network to open its full diagram.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {totalNetworks > 0 && (
            <div className="flex items-center gap-3 text-xs text-text-faint">
              <span><span className="stat-figure text-color-text">{totalNetworks}</span> networks</span>
              <span><span className="stat-figure text-color-text">{totalInstances}</span> instances</span>
              {totalIssues > 0 ? (
                <span className="inline-flex items-center gap-1" style={{ color: "var(--crit)" }}>
                  <AlertTriangle className="h-3 w-3" strokeWidth={2.5} />
                  <span className="stat-figure">{totalIssues}</span> issue{totalIssues === 1 ? "" : "s"}
                </span>
              ) : (
                <span style={{ color: "var(--ok)" }}>no issues</span>
              )}
            </div>
          )}
          <div className="relative w-[200px]">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" strokeWidth={2} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter networks…"
              className="w-full rounded-[var(--radius-control)] py-2 pl-8 pr-3 text-xs text-color-text outline-none transition-colors"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
            />
          </div>
        </div>
      </div>

      {isLoading && <div className="py-16 text-center text-sm text-text-faint">Loading network map…</div>}
      {error && (
        <div className="py-16 text-center text-sm" style={{ color: "var(--crit)" }}>
          Couldn&apos;t load the network topology map.
        </div>
      )}

      {data && (
        <>
          {providerNetworks.length === 0 && selfServiceNetworks.length === 0 ? (
            <div className="py-16 text-center text-sm text-text-faint">
              {query ? `No networks match "${query}".` : "No networks synced yet."}
            </div>
          ) : (
            <>
              {providerNetworks.length > 0 && (
                <div>
                  <div className="eyebrow mb-2">Provider networks</div>
                  <div className="flex items-start gap-5 overflow-x-auto pb-2">
                    {providerNetworks.map((net) => (
                      <NetworkColumn
                        key={net.id}
                        network={net}
                        color={colorForNetwork(net.id)}
                        routersById={routersById}
                        networksById={networksById}
                        onOpenDiagram={setDiagramNetworkId}
                      />
                    ))}
                  </div>
                </div>
              )}
              {selfServiceNetworks.length > 0 && (
                <div>
                  <div className="eyebrow mb-2">Self-service networks</div>
                  <div className="flex items-start gap-5 overflow-x-auto pb-2">
                    {selfServiceNetworks.map((net) => (
                      <NetworkColumn
                        key={net.id}
                        network={net}
                        color={colorForNetwork(net.id)}
                        routersById={routersById}
                        networksById={networksById}
                        onOpenDiagram={setDiagramNetworkId}
                      />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </>
      )}

      <AnimatePresence>
        {diagramNetworkId && (
          <NetworkTopologyDiagram networkId={diagramNetworkId} onClose={() => setDiagramNetworkId(null)} />
        )}
      </AnimatePresence>
    </Card>
  );
}
