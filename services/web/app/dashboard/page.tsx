"use client";

import CommandHeader from "@/components/dashboard/CommandHeader";
import NodeMetricsWidget from "@/components/dashboard/NodeMetricsWidget";
import IncidentsWidget from "@/components/dashboard/IncidentsWidget";
import BaselineHeatmapWidget from "@/components/dashboard/BaselineHeatmapWidget";
import LogsWidget from "@/components/dashboard/LogsWidget";
import SecurityWidget from "@/components/dashboard/SecurityWidget";
import NetworkWidget from "@/components/dashboard/NetworkWidget";
import CostWidget from "@/components/dashboard/CostWidget";
import RemediationWidget from "@/components/dashboard/RemediationWidget";
import NodesGrid from "@/components/dashboard/NodesGrid";
import ThresholdWarningsPanel from "@/components/ThresholdWarningsPanel";
import { useForecastWarnings } from "@/lib/useDashboardData";
import { useCurrentUser } from "@/lib/useCurrentUser";

/** One-page overview: every area of the cockpit as a small widget that links
 * to its full page. Each widget fetches its own data and degrades on its own,
 * so a single backend being down never blanks the dashboard. */
export default function DashboardPage() {
  const { data: warnings } = useForecastWarnings();
  const { user } = useCurrentUser();
  const canSeeRemediation = user?.role === "admin" || user?.role === "operator";

  return (
    <div className="cx-dash grid gap-4">
      <CommandHeader />

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <NodeMetricsWidget />
        <IncidentsWidget />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1.3fr]">
        <BaselineHeatmapWidget />
        <LogsWidget />
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <ThresholdWarningsPanel warnings={Array.isArray(warnings) ? warnings : []} />
        <SecurityWidget />
        <NetworkWidget />
      </div>

      <div className={`grid gap-4 ${canSeeRemediation ? "xl:grid-cols-2" : ""}`}>
        <CostWidget />
        {canSeeRemediation && <RemediationWidget />}
      </div>

      <NodesGrid />
    </div>
  );
}
