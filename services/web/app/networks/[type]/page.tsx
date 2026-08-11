"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { NETWORK_ENTITY_SLUGS } from "@/lib/entities";
import NetworkEntityView from "@/components/NetworkEntityView";
import TopologyHealthBadge from "@/components/TopologyHealthBadge";

const TITLES: Record<string, { title: string; description: string; placeholder: string }> = {
  networks: {
    title: "Networks",
    description: "Every Neutron network, with its subnets, gateway routers, floating IPs, and DHCP agents.",
    placeholder: "Search networks by name…",
  },
  subnets: {
    title: "Subnets",
    description: "CIDR blocks carved from a network, each with its own gateway IP.",
    placeholder: "Search subnets by name, CIDR, gateway IP…",
  },
  routers: {
    title: "Routers",
    description: "L3 routers, their external gateway network, and the agents hosting them.",
    placeholder: "Search routers by name…",
  },
  "floating-ips": {
    title: "Floating IPs",
    description: "Public IPs, each tied to a network and, if associated, a router.",
    placeholder: "Search floating IPs by address…",
  },
};

export default function NetworkEntityPage() {
  const { type } = useParams<{ type: string }>();
  const label = NETWORK_ENTITY_SLUGS[type];
  const meta = TITLES[type];

  if (!label || !meta) {
    return (
      <main className="grid gap-4">
        <div className="panel p-6">
          <p className="text-sm text-text-faint">
            Unknown network entity type &ldquo;{type}&rdquo;.{" "}
            <Link href="/networks" className="font-medium" style={{ color: "var(--accent)" }}>
              Back to Networks
            </Link>
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-start gap-3">
          <Link
            href="/networks"
            aria-label="Back to Networks"
            className="mt-0.5 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)] hover:text-color-text"
          >
            <ArrowLeft className="h-4.5 w-4.5" strokeWidth={2} />
          </Link>
          <div>
            <div className="eyebrow">Infrastructure · Networks</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">{meta.title}</h1>
            <p className="mt-1 text-sm text-text-faint">{meta.description}</p>
          </div>
        </div>
        <TopologyHealthBadge />
      </div>

      <NetworkEntityView label={label} placeholder={meta.placeholder} />
    </main>
  );
}
