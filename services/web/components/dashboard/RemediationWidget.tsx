"use client";

import useSWR from "swr";
import Link from "next/link";
import Widget, { Pill, Skeleton, Unavailable } from "./Widget";
import { apiFetcher } from "@/lib/useDashboardData";
import { useCurrentUser } from "@/lib/useCurrentUser";
import type { IncidentSummary } from "@/lib/types";
import { formatRelative } from "@/lib/anomalies";

const LABEL: Record<string, string> = { executed: "Executed", execution_failed: "Failed", rejected: "Rejected", approved: "Approved", executing: "Running", pending: "Awaiting approval" };
const color = (o: string) => (o === "executed" ? "var(--ok)" : o === "execution_failed" || o === "rejected" ? "var(--crit)" : o === "approved" || o === "executing" ? "var(--accent)" : "var(--warn)");

/** Recent AI-proposed fixes and what happened to them. Only admins/operators
 * can read the audit trail, so the widget is simply absent for viewers. */
export default function RemediationWidget() {
  const { user } = useCurrentUser();
  const allowed = user?.role === "admin" || user?.role === "operator";
  const { data, error, isLoading } = useSWR<IncidentSummary[]>(allowed ? "/api/remediation/audit/incidents?hours=168" : null, apiFetcher, { refreshInterval: 30_000, shouldRetryOnError: false });
  if (!allowed) return null;
  const list = (Array.isArray(data) ? data : []).slice(0, 4);
  const pending = list.filter((i) => i.outcome === "pending").length;

  return (
    <Widget eyebrow="Remediation" title="Copilot actions · 7 days" href="/remediation-audit" hrefLabel="Audit trail" flush
      right={pending ? <Pill color="var(--warn)" soft="var(--warn-soft)">{pending} to review</Pill> : null}>
      {isLoading ? (
        <div className="p-4 pt-0"><Skeleton h={130} /></div>
      ) : error ? (
        <Unavailable />
      ) : list.length === 0 ? (
        <Unavailable text="No remediation proposals this week." />
      ) : (
        <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
          {list.map((i) => (
            <Link key={i.trace_id} href={`/remediation-audit?trace=${encodeURIComponent(i.trace_id)}`} className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-[var(--canvas)]">
              <div className="min-w-0">
                <div className="truncate text-[13px] font-medium text-color-text">{i.hosts.slice(0, 2).join(", ") || "Fleet-wide"}</div>
                <div className="text-[11px] text-text-faint">{i.proposals} proposal{i.proposals === 1 ? "" : "s"}{i.last_at ? ` · ${formatRelative(i.last_at)}` : ""}</div>
              </div>
              <Pill color={color(i.outcome)} soft={`color-mix(in srgb, ${color(i.outcome)} 12%, transparent)`}>{LABEL[i.outcome] ?? i.outcome}</Pill>
            </Link>
          ))}
        </div>
      )}
    </Widget>
  );
}
