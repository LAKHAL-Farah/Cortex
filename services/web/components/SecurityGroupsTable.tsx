"use client";

import Link from "next/link";
import { AlertTriangle, ChevronRight, History, Network as NetworkIcon, ShieldOff } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { securityPillTone, flaggedSecurityGroupKeys, isSecurityGroupFlagged } from "@/lib/securityStatus";

/** Phase Sec-5: Notion-style dense table for /security/security-groups'
 * main view -- same row-per-host shape the previous tile-card grid showed
 * (status, every currently-attached group as a chip, risky/drift counts),
 * just laid out as ServiceTable.tsx's sticky-header/hairline-divider table
 * instead of cards. SecurityGroupsChart.tsx is the sibling "or view it as
 * a chart" toggle target on the same page. */
export default function SecurityGroupsTable({ findings }: { findings: SecurityFinding[] }) {
  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[1.6fr_1fr_2fr_1fr_1fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Host</div>
        <div>Status</div>
        <div>Groups</div>
        <div>Risky rules</div>
        <div className="text-right">Drift</div>
      </div>

      <div>
        {findings.map((f) => {
          const sig = f.raw_data.sec_group_signal;
          const tone = securityPillTone(sig);
          const riskyCount = sig.risky_rules?.length ?? null;
          const driftCount = sig.drift?.length ?? null;
          const groups = sig.data?.security_groups ?? null;
          const flagged = flaggedSecurityGroupKeys(sig);

          return (
            <Link
              key={f.hostname}
              href={`/security/security-groups/${encodeURIComponent(f.hostname)}`}
              className="group grid w-full gap-4 border-b p-5 text-left transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[1.6fr_1fr_2fr_1fr_1fr]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <div className="flex min-w-0 items-center gap-3">
                <span
                  className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                  style={{ background: `color-mix(in srgb, ${tone.color} 14%, transparent)` }}
                >
                  <NetworkIcon className="h-4 w-4" style={{ color: tone.color }} strokeWidth={1.75} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-medium text-color-text">{f.hostname}</div>
                  <div className="mt-0.5 truncate text-sm text-text-faint">{f.role}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <span
                  className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                  style={{ color: tone.color, background: tone.soft }}
                >
                  {tone.label}
                </span>
              </div>

              <div className="hidden flex-wrap items-center gap-1.5 sm:flex">
                {groups === null ? (
                  <span className="text-xs text-text-faint">Admin only</span>
                ) : groups.length === 0 ? (
                  <span className="text-xs text-text-faint">None attached</span>
                ) : (
                  groups.map((g) => {
                    const isFlagged = isSecurityGroupFlagged(g, flagged);
                    return (
                      <span
                        key={g.id}
                        title={`${g.rules.length} rule${g.rules.length === 1 ? "" : "s"}`}
                        className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium"
                        style={{
                          color: isFlagged ? "var(--crit)" : "var(--text-dim)",
                          background: isFlagged ? "var(--crit-soft)" : "var(--canvas)",
                        }}
                      >
                        {isFlagged && <AlertTriangle className="h-2.5 w-2.5" strokeWidth={2.5} />}
                        {g.name}
                      </span>
                    );
                  })
                )}
              </div>

              <div className="hidden items-center gap-1.5 text-sm text-text-dim sm:flex">
                <ShieldOff className="h-3.5 w-3.5" style={{ color: "var(--crit)" }} strokeWidth={2} />
                {riskyCount === null ? "—" : riskyCount}
              </div>

              <div className="flex items-center justify-end gap-3 text-sm text-text-dim">
                <span className="inline-flex items-center gap-1.5">
                  <History className="h-3.5 w-3.5" style={{ color: "var(--warn)" }} strokeWidth={2} />
                  {driftCount === null ? "—" : driftCount}
                </span>
                <ChevronRight
                  className="h-4 w-4 shrink-0 text-text-faint transition-transform group-hover:translate-x-0.5"
                  strokeWidth={2}
                />
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
