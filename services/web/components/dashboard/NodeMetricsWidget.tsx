"use client";

import Widget, { Skeleton, Unavailable } from "./Widget";
import AnalyticsChart from "@/components/AnalyticsChart";
import { useFleet } from "@/lib/useDashboardData";

/** Same per-node CPU / memory chart as the Metrics page ("Real-time node
 * metrics"), fed by the dashboard's 5s fleet poll and stretched to fill the
 * panel so it lines up with the incidents list beside it. */
export default function NodeMetricsWidget() {
  const { data: nodes, error, isLoading } = useFleet();
  const data = (nodes ?? []).map((n) => ({
    name: n.hostname,
    cpu: n.metrics?.cpu_percent ?? 0,
    memory: n.metrics?.memory_percent ?? 0,
  }));

  return (
    <Widget
      eyebrow="Metrics"
      title="Real-time node metrics"
      href="/metrics"
      hrefLabel="All metrics"
      fill
      right={
        <div className="hidden items-center gap-3 text-xs text-text-faint sm:flex">
          <span className="inline-flex items-center gap-1.5"><span className="status-dot" style={{ background: "var(--chart-1)" }} />CPU</span>
          <span className="inline-flex items-center gap-1.5"><span className="status-dot" style={{ background: "var(--chart-4)" }} />Memory</span>
        </div>
      }
    >
      {isLoading ? (
        <Skeleton h={260} />
      ) : error || data.length === 0 ? (
        <Unavailable text="No node metrics available right now." />
      ) : (
        <div className="relative min-h-[260px] flex-1">
          <div className="absolute inset-0">
            <AnalyticsChart data={data} height="100%" yDomain={[0, 100]} primaryColor="var(--chart-1)" secondaryColor="var(--chart-4)" />
          </div>
        </div>
      )}
    </Widget>
  );
}
