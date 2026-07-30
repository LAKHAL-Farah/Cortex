"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import NodeTable from "@/components/NodeTable";
import MetricCard from "@/components/ui/MetricCard";
import AnalyticsChart from "@/components/AnalyticsChart";
import RadialProgressCard from "@/components/ui/RadialProgressCard";
import type { DashboardNode } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function DashboardPage() {
  const [range, setRange] = useState("7d");
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

  if (isLoading) return <p className="p-6 text-text-faint">Loading…</p>;
  if (!nodes?.length) return <p className="p-6 text-text-faint">No nodes registered. Add one on the Nodes page.</p>;

  const sparklineData = nodes.map((node, index) => ({ name: String(index + 1), value: node.metrics?.cpu_percent ?? 0 }));
  const analyticsData = nodes.map((node) => ({ name: node.hostname, cpu: node.metrics?.cpu_percent ?? 0, memory: node.metrics?.memory_percent ?? 0 }));

  return (
    <main className="grid gap-6">
      <section className="rounded-[20px] border border-[#ECECEC] bg-white p-8 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-2xl">
            <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Overview</div>
            <h1 className="mt-3 text-4xl font-semibold text-color-text">Infrastructure insights.</h1>
            <p className="mt-4 max-w-xl text-base leading-7 text-text-dim">
              Monitor your fleet, understand trends, and take action from a clean operations workspace.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Link href="/nodes" className="inline-flex items-center justify-center rounded-[12px] bg-orange-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-orange-700">
              Add node
            </Link>
            <div className="inline-flex items-center gap-2 rounded-[12px] border border-[#ECECEC] bg-[#F8FAFC] px-4 py-3 text-sm text-text-faint">
              Live refresh every 5 seconds
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-4">
        <MetricCard
          title="Total nodes"
          value={nodes.length}
          trend={Math.round((metrics.active / Math.max(nodes.length, 1)) * 100)}
          trendLabel={`${metrics.active} healthy • ${metrics.offline} offline`}
          sparklineData={sparklineData}
          sparklineColor="#F97316"
        />
        <MetricCard
          title="Average CPU"
          value={`${metrics.avgCpu}%`}
          trend={3}
          trendLabel="7-day trend"
          sparklineData={sparklineData.map((item) => ({ ...item, value: item.value - 2 }))}
          sparklineColor="#10B981"
        />
        <MetricCard
          title="Average memory"
          value={`${metrics.avgMem}%`}
          trend={Math.round(metrics.avgMem - metrics.avgCpu)}
          trendLabel="Weekly usage"
          sparklineData={sparklineData.map((item) => ({ ...item, value: Math.max(item.value - 4, 0) }))}
          sparklineColor="#8B5CF6"
        />
        <MetricCard
          title="Alerts triggered"
          value={metrics.alerts}
          trend={metrics.alerts > 0 ? 1 : -1}
          trendLabel="Active warnings"
          sparklineData={sparklineData.map((item) => ({ ...item, value: Math.min(item.value + 8, 100) }))}
          sparklineColor="#F97316"
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[2.3fr_1fr]">
        <section className="rounded-[20px] border border-[#ECECEC] bg-white p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
          <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Live analytics</div>
              <h2 className="mt-3 text-2xl font-semibold text-color-text">Real-time node metrics</h2>
            </div>
            <div className="text-sm text-text-faint">Updated every 5 seconds</div>
          </div>

          <div className="mb-6 flex flex-wrap gap-4 rounded-[18px] border border-[#ECECEC] bg-[#F8FAFC] p-4 text-sm text-text-faint">
            <div className="inline-flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-orange-500" />
              CPU usage
            </div>
            <div className="inline-flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-purple-500" />
              Memory usage
            </div>
          </div>

          <AnalyticsChart data={analyticsData} />
        </section>

        <aside className="space-y-6">
          <RadialProgressCard
            label="Cluster health"
            value={Math.round((metrics.active / Math.max(nodes.length, 1)) * 100)}
            description={`${metrics.active} of ${nodes.length} nodes responding normally`}
          />

          <div className="rounded-[20px] border border-[#ECECEC] bg-white p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Status summary</div>
                <div className="mt-3 text-2xl font-semibold text-color-text">Core cluster metrics</div>
              </div>
              <div className="rounded-full bg-orange-50 px-3 py-2 text-sm font-semibold text-orange-600">Live</div>
            </div>
            <div className="mt-6 grid gap-4">
              <div className="rounded-[18px] bg-[#F8FAFC] p-4">
                <div className="text-sm text-text-faint">Memory pressure</div>
                <div className="mt-3 text-lg font-semibold text-color-text">{metrics.avgMem}%</div>
              </div>
              <div className="rounded-[18px] bg-[#F8FAFC] p-4">
                <div className="text-sm text-text-faint">CPU efficiency</div>
                <div className="mt-3 text-lg font-semibold text-color-text">{metrics.avgCpu}%</div>
              </div>
            </div>
          </div>
        </aside>
      </div>

      <section className="grid gap-6">
        <div className="rounded-[20px] border border-[#ECECEC] bg-white p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Fleet activity</div>
              <h2 className="mt-3 text-2xl font-semibold text-color-text">Active node inventory</h2>
            </div>
            <div className="inline-flex items-center gap-2 rounded-full bg-[#F8FAFC] px-4 py-3 text-sm text-text-faint">
              {metrics.active} active nodes
            </div>
          </div>
        </div>

        <NodeTable />
      </section>
    </main>
  );
}
