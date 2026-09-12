"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, RefreshCw, AlertTriangle, Network as NetworkIcon } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { securityPillTone } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** Per-host list, reusing GET /api/security/findings (already fetches
 * every node's four sub-checks in one pass) rather than a second
 * findings-list endpoint scoped to just sec_group_signal -- the payload
 * this page actually needs (sec_group_signal, has_signal, restricted) is
 * already a subset of what /findings returns. */
export default function SecurityGroupsListPage() {
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 20000, revalidateOnFocus: true },
  );

  const findings = data?.findings ?? [];

  return (
    <div className="space-y-6">
      <div>
        <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
          Back to Security
        </Link>
        <h1 className="font-display text-[22px] font-semibold text-color-text">Security groups</h1>
        <p className="mt-1 text-sm text-text-faint">
          Current Neutron security-group rules per host, audited against the built-in overly-permissive baseline,
          plus drift against the most recent stored snapshot (Phase Sec-1) -- a rule can show up as changed even
          when it isn&apos;t risky by itself.
        </p>
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

      <Card padding="p-0">
        <div className="flex items-center gap-2 border-b px-5 py-3" style={{ borderColor: "var(--border-soft)" }}>
          <NetworkIcon className="h-4 w-4" style={{ color: "var(--chart-3)" }} strokeWidth={2} />
          <span className="font-display text-[14px] font-semibold text-color-text">By host</span>
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 p-5 text-sm text-text-faint">
            <RefreshCw className="h-4 w-4 animate-spin" strokeWidth={2} />
            Checking every known node…
          </div>
        ) : findings.length === 0 ? (
          <div className="p-5 text-sm text-text-faint">No monitored nodes yet -- add one under Infrastructure → Nodes.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
                  <th className="px-5 py-2 font-medium">Host</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Risky rules</th>
                  <th className="px-3 py-2 font-medium">Drift since last snapshot</th>
                </tr>
              </thead>
              <tbody>
                {findings.map((f) => {
                  const sig = f.raw_data.sec_group_signal;
                  const tone = securityPillTone(sig);
                  const riskyCount = sig.risky_rules?.length ?? null;
                  const driftCount = sig.drift?.length ?? null;
                  return (
                    <tr key={f.hostname} className="border-b last:border-0" style={{ borderColor: "var(--border-soft)" }}>
                      <td className="px-5 py-2.5">
                        <Link href={`/security/security-groups/${encodeURIComponent(f.hostname)}`} className="font-medium text-color-text hover:underline">
                          {f.hostname}
                        </Link>
                        <span className="ml-2 text-xs text-text-faint">{f.role}</span>
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                          style={{ color: tone.color, background: tone.soft }}
                        >
                          {tone.label}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 tabular-nums text-text-dim">{riskyCount === null ? "—" : riskyCount}</td>
                      <td className="px-3 py-2.5 tabular-nums text-text-dim">{driftCount === null ? "—" : driftCount}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
