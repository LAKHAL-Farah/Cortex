"use client";

import { useMemo, useState } from "react";
import { AnimatePresence } from "framer-motion";
import useSWR from "swr";
import type { TopologyGraph as TopologyGraphData } from "@/lib/types";
import { buildGraphIndex, deriveServiceRows, SERVICE_SOURCE_LABEL, SERVICE_STATE_LABEL, type ServiceRow } from "@/lib/entities";
import EntityToolbar, { type EntityView } from "./EntityToolbar";
import ServiceCard from "./ServiceCard";
import ServiceTable from "./ServiceTable";
import TopologyDetailPanel from "./TopologyDetailPanel";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

function matchesQuery(s: ServiceRow, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return (
    s.id.toLowerCase().includes(needle) ||
    (s.binary ?? "").toLowerCase().includes(needle) ||
    (s.host ?? "").toLowerCase().includes(needle) ||
    (s.backend ?? "").toLowerCase().includes(needle) ||
    (s.zone ?? "").toLowerCase().includes(needle) ||
    (s.hostNode?.name ?? "").toLowerCase().includes(needle)
  );
}

/** Same graph the Topology page renders (`/api/topology`), just reduced to
 * :Service vertices and rendered as cards/table instead of nodes on a
 * canvas -- see lib/entities.ts for why this reads the full graph rather
 * than the narrower /api/v1/topology/services endpoint. */
export default function ServicesView() {
  const { data, error, isLoading } = useSWR<TopologyGraphData>("/api/topology", fetcher, {
    refreshInterval: 10000,
  });

  const [query, setQuery] = useState("");
  // Notion-style dense table reads better than cards as the landing view
  // for a resource list -- cards stay one click away via the toggle.
  const [view, setView] = useState<EntityView>("table");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [stateFilter, setStateFilter] = useState("all");
  const [hostFilter, setHostFilter] = useState("all");
  const [selectedVertex, setSelectedVertex] = useState<string | null>(null);

  const services = useMemo(() => {
    if (!data) return [];
    const index = buildGraphIndex(data);
    return deriveServiceRows(data, index);
  }, [data]);

  const sourceOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of services) {
      const key = s.source ?? "unknown";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].map(([value, count]) => ({
      value,
      label: SERVICE_SOURCE_LABEL[value] ?? value,
      count,
    }));
  }, [services]);

  const stateOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of services) {
      const key = s.state ?? "unknown";
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].map(([value, count]) => ({
      value,
      label: SERVICE_STATE_LABEL[value] ?? "Unknown",
      count,
    }));
  }, [services]);

  const hostOptions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of services) {
      if (!s.hostNode) continue;
      counts.set(s.hostNode.id, (counts.get(s.hostNode.id) ?? 0) + 1);
    }
    return [...counts.entries()].map(([value, count]) => ({ value, label: value, count }));
  }, [services]);

  const filtered = useMemo(() => {
    return services.filter((s) => {
      if (sourceFilter !== "all" && (s.source ?? "unknown") !== sourceFilter) return false;
      if (stateFilter !== "all" && (s.state ?? "unknown") !== stateFilter) return false;
      if (hostFilter !== "all" && s.hostNode?.id !== hostFilter) return false;
      return matchesQuery(s, query);
    });
  }, [services, query, sourceFilter, stateFilter, hostFilter]);

  if (isLoading) {
    return <p className="panel p-6 text-sm text-text-faint">Loading services…</p>;
  }
  if (error) {
    return (
      <p className="panel p-6 text-sm" style={{ color: "var(--crit)" }}>
        Couldn&apos;t load the topology graph.
      </p>
    );
  }
  if (services.length === 0) {
    return <p className="panel p-6 text-sm text-text-faint">No services in the topology graph yet.</p>;
  }

  return (
    <div className="grid gap-4">
      <EntityToolbar
        query={query}
        onQueryChange={setQuery}
        placeholder="Search services by binary, host, zone…"
        view={view}
        onViewChange={setView}
        resultCount={filtered.length}
        resultNoun="service"
        filters={[
          { key: "source", label: "All sources", value: sourceFilter, options: sourceOptions, onChange: setSourceFilter },
          { key: "state", label: "All states", value: stateFilter, options: stateOptions, onChange: setStateFilter },
          { key: "host", label: "All hosts", value: hostFilter, options: hostOptions, onChange: setHostFilter },
        ]}
      />

      {filtered.length === 0 ? (
        <p className="panel p-6 text-sm text-text-faint">No services match these filters.</p>
      ) : view === "cards" ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((s) => (
            <ServiceCard key={s.id} service={s} onOpen={setSelectedVertex} />
          ))}
        </div>
      ) : (
        <ServiceTable services={filtered} onOpen={setSelectedVertex} />
      )}

      <AnimatePresence>
        {selectedVertex && (
          <TopologyDetailPanel vertexId={selectedVertex} onClose={() => setSelectedVertex(null)} />
        )}
      </AnimatePresence>
    </div>
  );
}
