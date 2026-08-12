"use client";

import useSWR from "swr";
import { RefreshCw } from "lucide-react";
import type { TopologyHealth } from "@/lib/types";
import { SYNC_STATUS_COLOR, SYNC_STATUS_LABEL, SYNC_STATUS_SOFT, formatRelative } from "@/lib/topology";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** Most recent `finished_at` across both sync loops (openstack,
 * prometheus_health) -- see schemas.TopologyHealthOut. Neither sync having
 * ever run yet (fresh deploy) is the only case this returns null for. */
function latestFinishedAt(health: TopologyHealth): string | null {
  let latest: string | null = null;
  for (const run of Object.values(health.syncs)) {
    if (!run) continue;
    if (!latest || run.finished_at > latest) latest = run.finished_at;
  }
  return latest;
}

/** "synced Xm ago" badge, consistent with how Alerts/Baselines already
 * surface data freshness (lib/anomalies.ts::formatRelative). Backed by
 * GET /api/v1/topology/health, i.e. actual sync-run history rather than a
 * live Neo4j query -- see routers/topology.py's get_topology_health
 * docstring for why that distinction matters. */
export default function TopologyHealthBadge() {
  const { data, error, isLoading } = useSWR<TopologyHealth>("/api/topology/health", fetcher, {
    refreshInterval: 15000,
  });

  if (isLoading) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-text-faint">
        <RefreshCw className="h-3 w-3 animate-spin" strokeWidth={2} />
        Checking sync…
      </span>
    );
  }

  if (error || !data) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
        style={{ color: "var(--crit)", background: "var(--crit-soft)" }}
        title="Could not reach GET /api/v1/topology/health"
      >
        <span className="status-dot" style={{ background: "var(--crit)" }} />
        Health unavailable
      </span>
    );
  }

  const latest = latestFinishedAt(data);
  const label = SYNC_STATUS_LABEL[data.status];
  const color = SYNC_STATUS_COLOR[data.status];
  const soft = SYNC_STATUS_SOFT[data.status];

  const syncEntries = Object.entries(data.syncs);
  const tooltip = syncEntries
    .map(([type, run]) => `${type}: ${run ? `${run.status} (finished ${formatRelative(run.finished_at)})` : "no run yet"}`)
    .join(" · ");

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ color, background: soft }}
      title={tooltip}
    >
      <span
        className={data.status === "ok" ? "status-dot glow-pulse" : "status-dot"}
        style={{ background: color, ["--pulse-color" as string]: color }}
      />
      {latest ? `Synced ${formatRelative(latest)}` : label}
    </span>
  );
}
