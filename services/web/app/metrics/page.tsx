"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Plus } from "lucide-react";
import NodeCard from "@/components/NodeCard";
import MetricCard from "@/components/ui/MetricCard";
import AnalyticsChart from "@/components/AnalyticsChart";
import RadialProgressCard from "@/components/ui/RadialProgressCard";
import type { DashboardNode } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function MetricsPage() {
  const { data: nodes, isLoading } = useSWR<DashboardNode[]>("/api/dashboard", fetcher, {
    refreshInterval: 5000,
  });

  const metrics = useMemo(() => {
    if (!nodes?.length) return { active: 0, offline: 0, avgCpu: 0, avgMem: 0, alerts: 0, healthyNodes: 0 };
    const healthyNodes = nodes.filter((node) => node.metrics?.status === "up").length;
    const offline = nodes.length - healthyNodes;
    const avgCpu = Math.round((nodes.reduce((sum, node) => sum + (node.metrics?.cpu_percent || 0), 0) / nodes.length) * 10) / 10;
    const avgMem = Math.round((nodes.reduce((sum, node) => sum + (node.metrics?.memory_percent || 0), 0) / nodes.length) * 10) / 10;
    const alerts = nodes.reduce((count, node) => count + ((node.metrics?.health === "warning" || node.metrics?.health === "critical") ? 1 : 0), 0);
    return { active: healthyNodes, offline, avgCpu, avgMem, alerts, healthyNodes };
  }, [nodes]);

  if (isLoading) return <p className="p-6 text-sm text-text-faint">Loading…</p>;
  if (!nodes?.length) return <p className="p-6 text-sm text-text-faint">No nodes registered. Add one on the Nodes page.</p>;

  const sparklineData = nodes.map((node, index) => ({ name: String(index + 1), value: node.metrics?.cpu_percent ?? 0 }));
  const analyticsData = nodes.map((node) => ({ name: node.hostname, cpu: node.metrics?.cpu_percent ?? 0, memory: node.metrics?.memory_percent ?? 0 }));

  return (
    <main className="grid gap-4">
      <section className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="eyebrow">Overview</div>
          <h2 className="font-display mt-1 text-lg font-semibold text-color-text">Infrastructure insights</h2>
          <p className="mt-1 max-w-xl text-sm leading-6 text-text-faint">
            Monitor your fleet, understand trends, and take action from a single workspace.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href="/nodes"
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
            style={{ background: "var(--accent)" }}
          >
            <Plus className="h-4 w-4" strokeWidth={2} />
            Add node
          </Link>
          <div className="inline-flex items-center gap-2 rounded-[var(--radius-control)] px-3.5 py-2 text-sm text-text-faint" style={{ border: "1px solid var(--border)" }}>
            <span className="status-dot" style={{ background: "var(--ok)" }} />
            Live · refreshes every 5s
          </div>
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-4">
        <MetricCard
          title="Total nodes"
          value={nodes.length}
          trend={Math.round((metrics.active / Math.max(nodes.length, 1)) * 100)}
          trendLabel={`${metrics.active} healthy · ${metrics.offline} offline`}
          sparklineData={sparklineData}
          sparklineColor="var(--chart-1)"
        />
        <MetricCard
          title="Average CPU"
          value={`${metrics.avgCpu}%`}
          trend={3}
          trendLabel="7-day trend"
          sparklineData={sparklineData.map((item) => ({ ...item, value: item.value - 2 }))}
          sparklineColor="var(--chart-3)"
        />
        <MetricCard
          title="Average memory"
          value={`${metrics.avgMem}%`}
          trend={Math.round(metrics.avgMem - metrics.avgCpu)}
          trendLabel="Weekly usage"
          sparklineData={sparklineData.map((item) => ({ ...item, value: Math.max(item.value - 4, 0) }))}
          sparklineColor="var(--chart-4)"
        />
        <MetricCard
          title="Alerts triggered"
          value={metrics.alerts}
          trend={metrics.alerts > 0 ? 1 : -1}
          trendLabel="Active warnings"
          sparklineData={sparklineData.map((item) => ({ ...item, value: Math.min(item.value + 8, 100) }))}
          sparklineColor="var(--chart-5)"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[2.3fr_1fr]">
        <section className="panel p-5">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="eyebrow">Live analytics</div>
              <h3 className="mt-1 text-[15px] font-semibold text-color-text">Real-time node metrics</h3>
            </div>
            <div className="flex items-center gap-4 text-xs text-text-faint">
              <span className="inline-flex items-center gap-1.5">
                <span className="status-dot" style={{ background: "var(--chart-1)" }} />
                CPU
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="status-dot" style={{ background: "var(--chart-4)" }} />
                Memory
              </span>
            </div>
          </div>

          <AnalyticsChart data={analyticsData} primaryColor="var(--chart-1)" secondaryColor="var(--chart-4)" />
        </section>

        <aside className="space-y-4">
          <RadialProgressCard
            label="Cluster health"
            value={Math.round((metrics.active / Math.max(nodes.length, 1)) * 100)}
            description={`${metrics.active} of ${nodes.length} nodes responding normally`}
          />

          <div className="panel p-5">
            <div className="flex items-center justify-between gap-4">
              <div className="eyebrow">Status summary</div>
              <span
                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                style={{ color: "var(--ok)", background: "var(--ok-soft)" }}
              >
                <span className="status-dot" style={{ background: "var(--ok)" }} />
                Live
              </span>
            </div>
            <div className="mt-3.5 grid gap-2.5">
              <div className="rounded-[var(--radius-control)] p-3.5" style={{ background: "var(--canvas)" }}>
                <div className="text-xs text-text-faint">Memory pressure</div>
                <div className="stat-figure mt-1.5 text-lg text-color-text">{metrics.avgMem}%</div>
              </div>
              <div className="rounded-[var(--radius-control)] p-3.5" style={{ background: "var(--canvas)" }}>
                <div className="text-xs text-text-faint">CPU efficiency</div>
                <div className="stat-figure mt-1.5 text-lg text-color-text">{metrics.avgCpu}%</div>
              </div>
            </div>
          </div>
        </aside>
      </div>

      <section className="grid gap-4">
        <div className="panel flex flex-col gap-3 p-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="eyebrow">Fleet activity</div>
            <h3 className="mt-1 text-[15px] font-semibold text-color-text">Active node inventory</h3>
          </div>
          <div className="text-sm text-text-faint">{metrics.active} active nodes</div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-2">
          {nodes.map((node) => (
            <NodeCard key={node.id} n={node} />
          ))}
        </div>
      </section>
    </main>
  );
}
