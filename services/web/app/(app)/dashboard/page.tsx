"use client";

import { useMemo } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Plus } from "lucide-react";
import NodeOverviewCard from "@/components/NodeOverviewCard";
import MetricCard from "@/components/ui/MetricCard";
import AnalyticsChart from "@/components/AnalyticsChart";
import RadialProgressCard from "@/components/ui/RadialProgressCard";
import RecentIssuesPanel, { type Issue } from "@/components/RecentIssuesPanel";
import ThresholdWarningsPanel from "@/components/ThresholdWarningsPanel";
import { parseLevel } from "@/lib/logs";
import type { DashboardNode, LogEntry, ThresholdWarning } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function DashboardPage() {
  const { data: nodes, isLoading } = useSWR<DashboardNode[]>("/api/dashboard", fetcher, {
    refreshInterval: 5000,
  });
  // Recent-issues widget scans the last hour of logs client-side rather than
  // adding a second "important logs only" backend endpoint; /api/v1/logs
  // already returns newest-first, so this is just a cheap filter+slice.
  const { data: logEntries } = useSWR<LogEntry[]>("/api/logs?minutes=60&limit=300", fetcher, {
    refreshInterval: 5000,
  });
  // Threshold-breach ETA warnings (2.5) -- already filtered to
  // will_breach === true and sorted soonest-first by the API, so this is
  // ready to render as-is. Refreshes on the same cadence as the rest of the
  // dashboard rather than every forecast retrain (hourly), which is plenty.
  const { data: thresholdWarnings } = useSWR<ThresholdWarning[]>("/api/forecast/warnings", fetcher, {
    refreshInterval: 30000,
  });

  const metrics = useMemo(() => {
    if (!nodes?.length) return { active: 0, offline: 0, avgCpu: 0, avgMem: 0, healthyNodes: 0 };
    const healthyNodes = nodes.filter((node) => node.metrics?.status === "up").length;
    const offline = nodes.length - healthyNodes;
    const avgCpu = Math.round((nodes.reduce((sum, node) => sum + (node.metrics?.cpu_percent || 0), 0) / nodes.length) * 10) / 10;
    const avgMem = Math.round((nodes.reduce((sum, node) => sum + (node.metrics?.memory_percent || 0), 0) / nodes.length) * 10) / 10;
    return { active: healthyNodes, offline, avgCpu, avgMem, healthyNodes };
  }, [nodes]);

  const issues: Issue[] = useMemo(() => {
    if (!logEntries) return [];
    return logEntries
      .map((entry) => ({ ...entry, level: parseLevel(entry.line) }))
      .filter((entry): entry is Issue => entry.level === "ERROR" || entry.level === "WARNING");
  }, [logEntries]);

  const issueCountsByHost = useMemo(() => {
    const map: Record<string, number> = {};
    for (const issue of issues) {
      if (!issue.host) continue;
      map[issue.host] = (map[issue.host] ?? 0) + 1;
    }
    return map;
  }, [issues]);

  if (isLoading) return <p className="p-6 text-sm text-text-faint">Loading…</p>;
  if (!nodes?.length) return <p className="p-6 text-sm text-text-faint">No nodes registered. Add one on the Nodes page.</p>;

  const sparklineData = nodes.map((node, index) => ({ name: String(index + 1), value: node.metrics?.cpu_percent ?? 0 }));
  const analyticsData = nodes.map((node) => ({ name: node.hostname, cpu: node.metrics?.cpu_percent ?? 0, memory: node.metrics?.memory_percent ?? 0 }));
  const topIssues = issues.slice(0, 6);

  return (
    <main className="grid gap-4">
      <section className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="eyebrow">Home</div>
          <h2 className="font-display mt-1 text-lg font-semibold text-color-text">Fleet overview</h2>
          <p className="mt-1 max-w-xl text-sm leading-6 text-text-faint">
            Live health, trends, and the issues that need attention — all in one place.
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
          trendLabel="Across the fleet"
          sparklineData={sparklineData.map((item) => ({ ...item, value: item.value - 2 }))}
          sparklineColor="var(--chart-3)"
        />
        <MetricCard
          title="Average memory"
          value={`${metrics.avgMem}%`}
          trend={Math.round(metrics.avgMem - metrics.avgCpu)}
          trendLabel="Across the fleet"
          sparklineData={sparklineData.map((item) => ({ ...item, value: Math.max(item.value - 4, 0) }))}
          sparklineColor="var(--chart-4)"
        />
        <MetricCard
          title="Open issues"
          value={issues.length}
          trend={issues.length > 0 ? 1 : -1}
          trendLabel="Warnings & errors · last hour"
          sparklineData={sparklineData.map((item) => ({ ...item, value: Math.min(item.value + 8, 100) }))}
          sparklineColor="var(--crit)"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[2.1fr_1fr]">
        <section className="panel p-5">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="eyebrow">Live analytics</div>
              <h3 className="mt-1 text-[15px] font-semibold text-color-text">Fleet CPU & memory</h3>
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

        <aside className="grid gap-4">
          <RadialProgressCard
            label="Cluster health"
            value={Math.round((metrics.active / Math.max(nodes.length, 1)) * 100)}
            description={`${metrics.active} of ${nodes.length} nodes responding normally`}
          />
          <ThresholdWarningsPanel warnings={thresholdWarnings ?? []} />
          <RecentIssuesPanel issues={topIssues} totalCount={issues.length} />
        </aside>
      </div>

      <section className="grid gap-4">
        <div className="panel flex flex-col gap-3 p-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="eyebrow">Fleet activity</div>
            <h3 className="mt-1 text-[15px] font-semibold text-color-text">Nodes</h3>
          </div>
          <div className="text-sm text-text-faint">{metrics.active} active nodes</div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {nodes.map((node) => (
            <NodeOverviewCard key={node.id} n={node} issueCount={issueCountsByHost[node.hostname] ?? 0} />
          ))}
        </div>
      </section>
    </main>
  );
}
