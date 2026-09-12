"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, RefreshCw, AlertTriangle, Network as NetworkIcon, ChevronRight, ShieldOff, History } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { securityPillTone, flaggedSecurityGroupKeys, isSecurityGroupFlagged } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";

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
 * Phase Sec-4: rendered as a grid of "tile-card" cards (see globals.css)
 * instead of a table row per host -- each card carries the same tone
 * color the host's overall pill uses through into a small icon glow, and
 * shows every group actually attached to that host as its own chip
 * (flagged in red when it's the specific group carrying the signal)
 * rather than only an aggregate risky/drift count.
 */
export default function SecurityGroupsListPage() {
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const findings = data?.findings ?? [];

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
            risky by itself. Every group chip below is a group actually attached to that host right now; the
            specific one(s) carrying a signal are flagged red instead of only showing an aggregate count.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          <SecurityHealthBadge />
          <SecurityRescanButton />
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
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {findings.map((f) => {
            const sig = f.raw_data.sec_group_signal;
            const tone = securityPillTone(sig);
            const riskyCount = sig.risky_rules?.length ?? null;
            const driftCount = sig.drift?.length ?? null;
            const groups = sig.data?.security_groups ?? null;
            const flagged = flaggedSecurityGroupKeys(sig);
            return (
              <Link key={f.hostname} href={`/security/security-groups/${encodeURIComponent(f.hostname)}`} className="block">
                <div
                  className="tile-card tile-card--interactive flex h-full flex-col gap-4 p-5"
                  style={{ ["--tile-color" as string]: tone.color }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="tile-icon h-11 w-11">
                        <NetworkIcon className="h-5 w-5" style={{ color: tone.color }} strokeWidth={2} />
                      </span>
                      <div>
                        <div className="font-display text-[15px] font-semibold text-color-text">{f.hostname}</div>
                        <div className="text-xs text-text-faint">{f.role}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                        style={{ color: tone.color, background: tone.soft }}
                      >
                        {tone.label}
                      </span>
                      <ChevronRight className="h-4 w-4 shrink-0 text-text-faint" strokeWidth={2} />
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1.5 border-t pt-3" style={{ borderColor: "var(--border-soft)" }}>
                    {groups === null ? (
                      <span className="text-xs text-text-faint">Group details are admin-only.</span>
                    ) : groups.length === 0 ? (
                      <span className="text-xs text-text-faint">No security group attached.</span>
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

                  <div className="flex items-center gap-4 text-xs text-text-dim">
                    <span className="flex items-center gap-1.5">
                      <ShieldOff className="h-3.5 w-3.5" style={{ color: "var(--crit)" }} strokeWidth={2} />
                      {riskyCount === null ? "—" : riskyCount} risky rule{riskyCount === 1 ? "" : "s"}
                    </span>
                    <span className="flex items-center gap-1.5">
                      <History className="h-3.5 w-3.5" style={{ color: "var(--warn)" }} strokeWidth={2} />
                      {driftCount === null ? "—" : driftCount} changed since last snapshot
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
