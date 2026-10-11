"use client";

import Link from "next/link";
import { CheckCircle2, Sparkles } from "lucide-react";
import Widget, { Pill, Skeleton, Unavailable } from "./Widget";
import { copilotHref, useIncidents } from "@/lib/useDashboardData";
import { SEVERITY_COLOR, SEVERITY_LABEL, SEVERITY_SOFT, serviceDisplayName } from "@/lib/anomalies";

export default function IncidentsWidget() {
  const { data, error, isLoading } = useIncidents();
  const rank = { critical: 0, high: 1, medium: 2 } as const;
  const sorted = [...(data ?? [])].sort((a, b) => rank[a.severity] - rank[b.severity]);
  const list = sorted.slice(0, 3);
  const more = sorted.length - list.length;

  return (
    <Widget eyebrow="Alerts" title="Active incidents" href="/alerts" hrefLabel="View all" flush>
      {isLoading ? (
        <div className="p-4 pt-0"><Skeleton h={180} /></div>
      ) : error ? (
        <Unavailable />
      ) : list.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
          <CheckCircle2 className="h-6 w-6" style={{ color: "var(--ok)" }} strokeWidth={1.5} />
          <p className="text-sm text-text-faint">No correlated incidents. Everything is within baseline.</p>
        </div>
      ) : (
        <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
          {list.map((i) => {
            const root = i.root_cause_guess ? serviceDisplayName(i.root_cause_guess.vertex_id) : (i.members[0]?.hostname ?? "Unknown");
            return (
              <div key={i.incident_id} className="px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <Pill color={SEVERITY_COLOR[i.severity]} soft={SEVERITY_SOFT[i.severity]}>{SEVERITY_LABEL[i.severity]}</Pill>
                  <span className="text-[11px] text-text-faint">{i.member_count} alert{i.member_count === 1 ? "" : "s"}</span>
                </div>
                <div className="mt-1.5 truncate text-[13px] font-medium text-color-text">Likely root: {root}</div>
                <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-text-faint">{i.narrative}</p>
                <Link
                  href={copilotHref(`Investigate incident ${i.incident_id} (root cause guess: ${root}) and propose a remediation`)}
                  className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium"
                  style={{ color: "var(--accent)" }}
                >
                  <Sparkles className="h-3 w-3" strokeWidth={1.75} />
                  Investigate with Copilot
                </Link>
              </div>
            );
          })}
          {more > 0 && (
            <Link href="/alerts" className="block px-4 py-2.5 text-xs font-medium text-text-faint transition-colors hover:text-color-text">
              +{more} more incident{more === 1 ? "" : "s"}
            </Link>
          )}
        </div>
      )}
    </Widget>
  );
}
