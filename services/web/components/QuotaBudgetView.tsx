"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { Bell, FileText, LayoutDashboard, Table2 } from "lucide-react";
import QuotaResyncButton from "./QuotaResyncButton";
import FinopsOverview from "./FinopsOverview";
import FinopsProjects from "./FinopsProjects";
import FinopsReports from "./FinopsReports";
import QuotaAlertsPanel from "./QuotaAlertsPanel";

type Tab = "overview" | "projects" | "alerts" | "reports";

const TABS: { id: Tab; label: string; icon: typeof Bell }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "projects", label: "Projects", icon: Table2 },
  { id: "alerts", label: "Alerts", icon: Bell },
  { id: "reports", label: "Reports", icon: FileText },
];

/** Quotas & Budget -- FinOps for a *private* cloud.
 *
 * A self-hosted OpenStack has a fixed monthly bill, not a per-unit price
 * list, so the page answers the cloud owner's actual questions instead of
 * only listing threshold breaches:
 *  - Overview: how much of what we pay for is reserved vs idle, and are we
 *    promising (quotas) more than we own?
 *  - Projects: every project's usage, quota headroom, cost and budget.
 *  - Alerts:   capacity-cap vs budget-cap breaches (roadmap 2.9).
 *  - Reports:  showback/chargeback by project and department, PDF/CSV.
 * See services/api/app/services/finops.py for the cost model.
 */
export default function QuotaBudgetView() {
  const [tab, setTab] = useState<Tab>("overview");
  const { mutate } = useSWRConfig();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-[22px] font-semibold text-color-text">Quotas &amp; Budget</h1>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            FinOps for a private cloud: a fixed monthly bill shared between projects. See what is reserved vs idle,
            who is consuming the platform, and what each project and department should be charged.
          </p>
        </div>
        <QuotaResyncButton
          onDone={() => {
            mutate("/api/quotas/overview");
            mutate("/api/quotas/alerts");
          }}
        />
      </div>

      <div className="flex gap-1 border-b" style={{ borderColor: "var(--border-soft)" }} role="tablist">
        {TABS.map(({ id, label, icon: Icon }) => {
          const active = tab === id;
          return (
            <button
              key={id}
              role="tab"
              aria-selected={active}
              onClick={() => setTab(id)}
              className="-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors"
              style={{
                borderColor: active ? "var(--accent)" : "transparent",
                color: active ? "var(--text)" : "var(--text-faint)",
              }}
            >
              <Icon className="h-3.5 w-3.5" strokeWidth={2} />
              {label}
            </button>
          );
        })}
      </div>

      {tab === "overview" && <FinopsOverview />}
      {tab === "projects" && <FinopsProjects />}
      {tab === "alerts" && <QuotaAlertsPanel />}
      {tab === "reports" && <FinopsReports />}
    </div>
  );
}
