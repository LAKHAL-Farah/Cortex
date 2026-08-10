"use client";

import { useState } from "react";
import { AnimatePresence } from "framer-motion";
import { Waypoints } from "lucide-react";
import TopologyGraph from "@/components/TopologyGraph";
import TopologyDetailPanel from "@/components/TopologyDetailPanel";
import TopologyHealthBadge from "@/components/TopologyHealthBadge";

export default function TopologyPage() {
  const [selectedVertex, setSelectedVertex] = useState<string | null>(null);

  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-start gap-3">
          <span
            className="mt-0.5 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
            style={{ background: "var(--accent-soft)" }}
          >
            <Waypoints className="h-4.5 w-4.5" style={{ color: "var(--accent)" }} strokeWidth={2} />
          </span>
          <div>
            <div className="eyebrow">Infrastructure</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Topology</h1>
            <p className="mt-1 text-sm text-text-faint">
              Who talks to whom: hypervisors, OpenStack services, and networks, synced from Neo4j.
            </p>
          </div>
        </div>
        <TopologyHealthBadge />
      </div>

      <TopologyGraph onSelectVertex={setSelectedVertex} />

      <AnimatePresence>
        {selectedVertex && (
          <TopologyDetailPanel vertexId={selectedVertex} onClose={() => setSelectedVertex(null)} />
        )}
      </AnimatePresence>
    </main>
  );
}
