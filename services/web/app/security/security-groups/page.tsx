"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, RefreshCw, AlertTriangle, Rows3, BarChart3 } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";
import SecurityGroupsTable from "@/components/SecurityGroupsTable";
import SecurityGroupsChart from "@/components/SecurityGroupsChart";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** Per-host list, reusing GET /api/security/findings (already fetches
 * every node's four sub-checks in one pass) rather than a second
 * findings-list endpoint scoped to just sec_group_signal -- the payload
 * this page actually needs (sec_group_signal, has_signal, restricted) is
 * already a subset of what /findings returns.
 *
 * Phase Sec-5: main view is a Notion-style table (SecurityGroupsTable,
 * same row-per-host shape the previous tile-card grid showed: status,
 * every group actually attached as a chip flagged red when it carries the
 * signal, risky/drift counts) with a Table/Chart toggle -- Chart
 * (SecurityGroupsChart) plots the same risky-rules/drift counts as bars
 * per host for a fleet-wide read instead of scanning rows one at a time.
 */
export default function SecurityGroupsListPage() {
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const findings = data?.findings ?? [];
  const [view, setView] = useState<"table" | "chart">("table");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <h1 className="font-display text-[22px] font-semibold text-color-text">Security groups</h1>
          <p className="mt-1 text-sm text-text-faint">
            Current Neutron security-group rules per host, audited against the built-in overly-permissive baseline,
            plus drift against the most recent stored snapshot -- a rule can show up as changed even when it isn&apos;t
            risky by itself. OpenStack attaches security groups to an instance&apos;s own port, not to the compute
            node, so every group chip below is the union of what&apos;s attached to any VM actually scheduled onto
            that host right now; the specific one(s) carrying a signal are flagged red instead of only showing an
            aggregate count.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          <SecurityHealthBadge />
          <SecurityRescanButton />
          <div
            className="inline-flex flex-shrink-0 rounded-[var(--radius-control)] p-0.5"
            style={{ border: "1px solid var(--border)" }}
            role="group"
            aria-label="Switch view"
          >
            <button
              onClick={() => setView("table")}
              aria-pressed={view === "table"}
              title="Table view"
              type="button"
              className="inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1.5 text-xs font-medium transition-colors"
              style={{
                background: view === "table" ? "var(--accent)" : "transparent",
                color: view === "table" ? "#fff" : "var(--text-dim)",
              }}
            >
              <Rows3 className="h-3.5 w-3.5" strokeWidth={2} />
              Table
            </button>
            <button
              onClick={() => setView("chart")}
              aria-pressed={view === "chart"}
              title="Chart view"
              type="button"
              className="inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1.5 text-xs font-medium transition-colors"
              style={{
                background: view === "chart" ? "var(--accent)" : "transparent",
                color: view === "chart" ? "#fff" : "var(--text-dim)",
              }}
            >
              <BarChart3 className="h-3.5 w-3.5" strokeWidth={2} />
              Chart
            </button>
          </div>
        </div>
      </div>

      {error && (
        <Card>
          <div className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--crit)" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
            <span>Security-group findings are currently unavailable.</span>
            <button onClick={() => mutate()} className="ml-auto inline-flex items-center gap-1.5 font-medium underline underline-offset-2" type="button">
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
              Retry
            </button>
          </div>
        </Card>
      )}

      {isLoading ? (
        <Card>
          <div className="flex items-center gap-2 text-sm text-text-faint">
            <RefreshCw className="h-4 w-4 animate-spin" strokeWidth={2} />
            Checking every known node…
          </div>
        </Card>
      ) : findings.length === 0 ? (
        <Card>
          <div className="text-sm text-text-faint">No monitored nodes yet -- add one under Infrastructure → Nodes.</div>
        </Card>
      ) : view === "table" ? (
        <SecurityGroupsTable findings={findings} />
      ) : (
        <SecurityGroupsChart findings={findings} />
      )}
    </div>
  );
}
