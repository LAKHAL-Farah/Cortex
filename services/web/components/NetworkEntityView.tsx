"use client";

import { useMemo, useState } from "react";
import { AnimatePresence } from "framer-motion";
import useSWR from "swr";
import type { TopologyGraph as TopologyGraphData } from "@/lib/types";
import {
  buildGraphIndex,
  deriveNetworkEntityDisplayRows,
  type NetworkEntityDisplayRow,
  type NetworkEntityLabel,
} from "@/lib/entities";
import EntityToolbar, { type EntityView } from "./EntityToolbar";
import NetworkEntityCard from "./NetworkEntityCard";
import NetworkEntityTable from "./NetworkEntityTable";
import TopologyDetailPanel from "./TopologyDetailPanel";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

function matchesQuery(row: NetworkEntityDisplayRow, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (row.id.toLowerCase().includes(needle)) return true;
  if (row.title.toLowerCase().includes(needle)) return true;
  if (row.subtitle?.toLowerCase().includes(needle)) return true;
  if (row.relations.some((r) => r.ref?.name.toLowerCase().includes(needle))) return true;
  return false;
}

/** Reads the same full graph the Topology page and Services page use
 * (`/api/topology`), reduced to one vertex label and rendered as
 * cards/table -- the second of the two /networks pages the user picks a
 * category from. See lib/entities.ts::deriveNetworkEntityDisplayRows for the
 * per-label field/relation mapping. */
export default function NetworkEntityView({ label, placeholder }: { label: NetworkEntityLabel; placeholder: string }) {
  const { data, error, isLoading } = useSWR<TopologyGraphData>("/api/topology", fetcher, {
    refreshInterval: 10000,
  });

  const [query, setQuery] = useState("");
  // Table-first, same as ServicesView -- Notion-style dense rows by
  // default, cards still available via the toggle.
  const [view, setView] = useState<EntityView>("table");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selectedVertex, setSelectedVertex] = useState<string | null>(null);

  const rows = useMemo(() => {
    if (!data) return [];
    const index = buildGraphIndex(data);
    return deriveNetworkEntityDisplayRows(label, data, index);
  }, [data, label]);

  const hasStatus = rows.some((r) => r.status);
  const statusOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of rows) {
      if (!r.status) continue;
      counts.set(r.status, (counts.get(r.status) ?? 0) + 1);
    }
    return [...counts.entries()].map(([value, count]) => ({ value, label: value, count }));
  }, [rows]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      return matchesQuery(r, query);
    });
  }, [rows, query, statusFilter]);

  if (isLoading) {
    return <p className="panel p-6 text-sm text-text-faint">Loading…</p>;
  }
  if (error) {
    return (
      <p className="panel p-6 text-sm" style={{ color: "var(--crit)" }}>
        Couldn&apos;t load the topology graph.
      </p>
    );
  }
  if (rows.length === 0) {
    return <p className="panel p-6 text-sm text-text-faint">Nothing of this type in the topology graph yet.</p>;
  }

  return (
    <div className="grid gap-4">
      <EntityToolbar
        query={query}
        onQueryChange={setQuery}
        placeholder={placeholder}
        view={view}
        onViewChange={setView}
        resultCount={filtered.length}
        resultNoun={label.toLowerCase()}
        filters={
          hasStatus
            ? [{ key: "status", label: "All statuses", value: statusFilter, options: statusOptions, onChange: setStatusFilter }]
            : []
        }
      />

      {filtered.length === 0 ? (
        <p className="panel p-6 text-sm text-text-faint">No results match these filters.</p>
      ) : view === "cards" ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((row) => (
            <NetworkEntityCard key={row.id} row={row} onOpen={setSelectedVertex} />
          ))}
        </div>
      ) : (
        <NetworkEntityTable rows={filtered} onOpen={setSelectedVertex} />
      )}

      <AnimatePresence>
        {selectedVertex && (
          <TopologyDetailPanel vertexId={selectedVertex} onClose={() => setSelectedVertex(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}
