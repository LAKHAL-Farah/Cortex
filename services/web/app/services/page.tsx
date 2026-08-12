"use client";

import { Boxes } from "lucide-react";
import ServicesView from "@/components/ServicesView";
import TopologyHealthBadge from "@/components/TopologyHealthBadge";

export default function ServicesPage() {
  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-start gap-3">
          <span
            className="mt-0.5 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
            style={{ background: "color-mix(in srgb, var(--chart-2) 14%, transparent)" }}
          >
            <Boxes className="h-4.5 w-4.5" style={{ color: "var(--chart-2)" }} strokeWidth={2} />
          </span>
          <div>
            <div className="eyebrow">Infrastructure</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Services</h1>
            <p className="mt-1 text-sm text-text-faint">
              Nova, Cinder, and Neutron services, with the node each one runs on — synced from Neo4j.
            </p>
          </div>
        </div>
        <TopologyHealthBadge />
      </div>

      <ServicesView />
    </main>
  );
}
