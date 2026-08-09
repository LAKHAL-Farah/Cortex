"use client";

import { useState } from "react";
import TopologyGraph from "@/components/TopologyGraph";
import TopologyDetailPanel from "@/components/TopologyDetailPanel";
import TopologyHealthBadge from "@/components/TopologyHealthBadge";
import { EDGE_LABEL, LABEL_COLOR, ROLE_COLOR } from "@/lib/topology";

function LegendSwatch({ color, label, shape = "circle" }: { color: string; label: string; shape?: "circle" | "square" }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-text-faint">
      <span
        className={shape === "circle" ? "inline-block h-2.5 w-2.5 rounded-full" : "inline-block h-2.5 w-2.5"}
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

export default function TopologyPage() {
  const [selectedVertex, setSelectedVertex] = useState<string | null>(null);

  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div>
          <div className="eyebrow">Infrastructure</div>
          <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Topology</h1>
          <p className="mt-1 text-sm text-text-faint">
            Who talks to whom: hypervisors, OpenStack services, and networks, synced from Neo4j.
          </p>
        </div>
        <TopologyHealthBadge />
      </div>

      <div className="panel flex flex-wrap items-center gap-4 px-5 py-3">
        {Object.entries(ROLE_COLOR).map(([role, color]) => (
          <LegendSwatch key={role} color={color} label={role} />
        ))}
        {Object.entries(LABEL_COLOR).map(([label, color]) => (
          <LegendSwatch key={label} color={color} label={label} shape={label === "Network" || label === "Subnet" ? "square" : "circle"} />
        ))}
        <span className="ml-auto text-xs text-text-faint">
          {EDGE_LABEL.RUNS_ON} / {EDGE_LABEL.CONNECTS} = solid · {EDGE_LABEL.SERVES} = dashed
        </span>
      </div>

      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <TopologyGraph onSelectVertex={setSelectedVertex} />
        </div>
        {selectedVertex && (
          <TopologyDetailPanel vertexId={selectedVertex} onClose={() => setSelectedVertex(null)} />
        )}
      </div>
    </main>
  );
}
