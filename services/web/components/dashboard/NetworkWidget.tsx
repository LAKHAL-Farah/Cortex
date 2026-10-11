"use client";

import useSWR from "swr";
import Widget, { MiniStat, Pill, Skeleton, Unavailable } from "./Widget";
import { apiFetcher } from "@/lib/useDashboardData";
import type { NetworkHealth, TopologyHealth } from "@/lib/types";

export default function NetworkWidget() {
  const opts = { refreshInterval: 30_000, shouldRetryOnError: false } as const;
  const net = useSWR<NetworkHealth>("/api/network/health", apiFetcher, opts);
  const topo = useSWR<TopologyHealth>("/api/topology/health", apiFetcher, opts);
  const n = net.data;

  const reachable = (n?.latencies ?? []).filter((l) => l.reachable && l.latency_ms != null);
  const avgLatency = reachable.length ? reachable.reduce((s, l) => s + (l.latency_ms ?? 0), 0) / reachable.length : null;
  const unreachable = (n?.latencies ?? []).filter((l) => !l.reachable).length;
  const bad = (n?.routers_down.length ?? 0) + (n?.ports_down.length ?? 0) + unreachable;
  const tone = n?.status === "ok" && !bad ? { c: "var(--ok)", s: "var(--ok-soft)", l: "Healthy" } : { c: "var(--warn)", s: "var(--warn-soft)", l: "Degraded" };
  const topoColor = topo.data?.status === "ok" ? "var(--ok)" : topo.data ? "var(--warn)" : "var(--neutral)";

  return (
    <Widget eyebrow="Network" title="Health & topology" href="/networks" right={n ? <Pill color={tone.c} soft={tone.s}>{tone.l}</Pill> : null}>
      {net.isLoading ? (
        <Skeleton h={130} />
      ) : net.error ? (
        <Unavailable />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2">
            <MiniStat label="Routers down" value={n?.routers_down.length ?? 0} color={n?.routers_down.length ? "var(--crit)" : "var(--ok)"} />
            <MiniStat label="Ports down" value={n?.ports_down.length ?? 0} color={n?.ports_down.length ? "var(--crit)" : "var(--ok)"} />
            <MiniStat label="Orphan floating IPs" value={n?.floating_ips_orphaned.length ?? 0} color={n?.floating_ips_orphaned.length ? "var(--warn)" : undefined} />
            <MiniStat label="Avg node latency" value={avgLatency != null ? `${avgLatency.toFixed(0)} ms` : "—"} />
          </div>
          <div className="mt-3 flex items-center gap-2 text-[11px] text-text-faint">
            <span className="status-dot" style={{ background: topoColor }} />
            Topology sync: {topo.data?.status ?? "unknown"}
            {!n?.graph_available && <span>· graph unavailable</span>}
          </div>
        </>
      )}
    </Widget>
  );
}
