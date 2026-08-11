"use client";

import { useMemo } from "react";
import useSWR from "swr";
import { Network as NetworkIcon, Grid2x2, Router as RouterIcon, Globe } from "lucide-react";
import type { TopologyGraph as TopologyGraphData } from "@/lib/types";
import { slugForNetworkEntity } from "@/lib/entities";
import NetworkCategoryCard from "@/components/NetworkCategoryCard";
import TopologyHealthBadge from "@/components/TopologyHealthBadge";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

// Colors/icons match lib/topology.ts's LABEL_COLOR/LABEL_ICON exactly, so a
// Network/Subnet/Router/FloatingIP reads the same here as it does on the
// Topology graph and its detail panel -- one palette per vertex label
// across the whole app, not a second one invented for this page.
const CATEGORIES = [
  {
    vertexLabel: "Network" as const,
    slug: slugForNetworkEntity("Network"),
    label: "Networks",
    description: "Neutron networks, with every subnet, gateway router, and floating IP carved from them.",
    color: "var(--chart-3)",
    icon: NetworkIcon,
  },
  {
    vertexLabel: "Subnet" as const,
    slug: slugForNetworkEntity("Subnet"),
    label: "Subnets",
    description: "CIDR blocks carved from a network, each with a gateway IP.",
    color: "var(--chart-4)",
    icon: Grid2x2,
  },
  {
    vertexLabel: "Router" as const,
    slug: slugForNetworkEntity("Router"),
    label: "Routers",
    description: "L3 routers, their external gateway network, and the agents hosting them.",
    color: "var(--chart-5)",
    icon: RouterIcon,
  },
  {
    vertexLabel: "FloatingIP" as const,
    slug: slugForNetworkEntity("FloatingIP"),
    label: "Floating IPs",
    description: "Public IPs, each tied to a network and (if associated) a router.",
    color: "var(--medium)",
    icon: Globe,
  },
];

export default function NetworksPage() {
  const { data } = useSWR<TopologyGraphData>("/api/topology", fetcher, { refreshInterval: 15000 });

  const counts = useMemo(() => {
    const c = new Map<string, number>();
    for (const v of data?.nodes ?? []) c.set(v.label, (c.get(v.label) ?? 0) + 1);
    return c;
  }, [data]);

  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-start gap-3">
          <span
            className="mt-0.5 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
            style={{ background: "color-mix(in srgb, var(--chart-3) 14%, transparent)" }}
          >
            <NetworkIcon className="h-4.5 w-4.5" style={{ color: "var(--chart-3)" }} strokeWidth={2} />
          </span>
          <div>
            <div className="eyebrow">Infrastructure</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Networks</h1>
            <p className="mt-1 text-sm text-text-faint">
              Choose what to browse — networks, subnets, routers, or floating IPs — synced from Neo4j.
            </p>
          </div>
        </div>
        <TopologyHealthBadge />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {CATEGORIES.map((c) => (
          <NetworkCategoryCard
            key={c.slug}
            href={`/networks/${c.slug}`}
            label={c.label}
            description={c.description}
            color={c.color}
            icon={c.icon}
            count={counts.get(c.vertexLabel) ?? 0}
          />
        ))}
      </div>
    </main>
  );
}
