"use client";

import useSWR from "swr";
import Widget, { MiniStat, Pill, Skeleton, Unavailable } from "./Widget";
import { apiFetcher } from "@/lib/useDashboardData";
import type { FinopsOverview, QuotaAlert } from "@/lib/types";
import { formatEur, formatPct, projectColor } from "@/lib/quotas";

export default function CostWidget() {
  const opts = { refreshInterval: 60_000, shouldRetryOnError: false } as const;
  const overview = useSWR<FinopsOverview>("/api/quotas/overview", apiFetcher, opts);
  const alerts = useSWR<QuotaAlert[]>("/api/quotas/alerts", apiFetcher, opts);

  const breaches = (alerts.data ?? []).filter((a) => a.severity !== "normal");
  const crit = breaches.filter((a) => a.severity === "critical").length;
  const o = overview.data;
  const projects = o && o.available ? [...o.projects].sort((a, b) => b.cost.fully_loaded_eur - a.cost.fully_loaded_eur).slice(0, 3) : [];
  const total = o && o.available ? o.cloud.monthly_cost_eur : 0;

  return (
    <Widget
      eyebrow="FinOps"
      title="Cost & quotas"
      href="/quotas"
      right={<Pill color={crit ? "var(--crit)" : breaches.length ? "var(--warn)" : "var(--ok)"} soft={crit ? "var(--crit-soft)" : breaches.length ? "var(--warn-soft)" : "var(--ok-soft)"}>{breaches.length ? `${breaches.length} breach${breaches.length === 1 ? "" : "es"}` : "Within limits"}</Pill>}
    >
      {overview.isLoading ? (
        <Skeleton h={130} />
      ) : overview.error || !o ? (
        <Unavailable />
      ) : !o.available ? (
        <Unavailable text="Cost model not configured yet." />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2">
            <MiniStat label="Monthly cloud cost" value={formatEur(total, 0)} />
            <MiniStat label="Utilization" value={o.cloud.cost.utilization_pct != null ? formatPct(o.cloud.cost.utilization_pct) : "—"} color={o.cloud.oversold ? "var(--warn)" : undefined} />
          </div>
          <div className="mt-3 space-y-2">
            {projects.map((p, i) => {
              const share = Math.min(Math.max(p.cost.share_pct, 0), 100);
              return (
                <div key={p.project_id}>
                  <div className="flex items-center justify-between text-xs">
                    <span className="truncate text-color-text">{p.project_name}</span>
                    <span className="ml-2 flex-shrink-0 text-text-faint">{formatEur(p.cost.fully_loaded_eur, 0)}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full" style={{ background: "var(--canvas)" }}>
                    <div className="h-full rounded-full" style={{ width: `${share}%`, background: projectColor(i) }} />
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </Widget>
  );
}
