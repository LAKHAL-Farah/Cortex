"use client";

import { useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, AlertTriangle, RefreshCw, ShieldQuestion, ShieldCheck, ExternalLink } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";
import SecurityScopeTag from "@/components/SecurityScopeTag";
import AuthActivityTable from "@/components/AuthActivityTable";
import { buildAuthHostStatuses, flattenAuthFindings } from "@/lib/securityStatus";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** Phase Sec-6: real data, unblocked by the same Loki auth-log service the
 * generic Logs page (`/logs`, `LogViewer.tsx`) already reads --
 * `agents/nodes/security.py`'s `_check_auth_anomaly` sub-check queries the
 * last 60 minutes of each host's log stream for a fixed set of
 * auth-failure keywords (failed password, invalid user, permission
 * denied, repeated sudo failures, ...) and returns its 5 most recent
 * matches. Same GET /api/security/findings every other Security page
 * already polls -- auth_signal.entries is already part of that payload,
 * no new endpoint needed, same as /security/vulnerabilities reusing it
 * for cve_signal.
 *
 * This is deliberately *not* the same thing as the Anomaly agent's
 * odd-hour/baseline scoring over ssh_failed_logins_5min (see
 * `_check_auth_anomaly`'s own docstring) -- that's a statistical "is this
 * hour's login volume unusual for this host" judgment fed only to the
 * Anomaly agent; this is a plain "did a failed-login pattern show up in
 * the log" keyword match, fed only to Security. A host can trip one
 * without the other.
 *
 * Node-scoped (SecurityScopeTag): this reads a host's own auth log, never
 * anything inside a guest VM.
 */
export default function AuthActivityPage() {
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const findings = useMemo(() => data?.findings ?? [], [data]);
  const rows = useMemo(() => flattenAuthFindings(findings), [findings]);
  const hostStatuses = useMemo(() => buildAuthHostStatuses(findings), [findings]);

  const flaggedHosts = hostStatuses.filter((h) => h.tone.label === "Flagged");
  const degradedHosts = hostStatuses.filter((h) => h.tone.label === "Unknown");
  const restrictedHosts = hostStatuses.filter((h) => h.tone.label === "Admin only");
  const cleanHosts = hostStatuses.filter((h) => h.tone.label === "Clean");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">Auth activity</h1>
            <SecurityScopeTag scope="node" />
          </div>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            Correlated auth-failure log lines (failed password, invalid user, permission denied, repeated sudo
            failures) per host, pulled from the last 60 minutes via Loki -- a plain keyword match, not the Anomaly
            agent&apos;s separate odd-hour login-volume scoring, so a host can trip one without the other.
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
            <span>Auth-activity findings are currently unavailable.</span>
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
        <>
          <p className="px-1 text-sm text-text-faint">
            <span className="font-medium text-text-dim">{flaggedHosts.length}</span> of{" "}
            <span className="font-medium text-text-dim">{hostStatuses.length}</span> monitored host
            {hostStatuses.length === 1 ? "" : "s"} show correlated auth-failure activity in the last 60 minutes.
          </p>

          {(degradedHosts.length > 0 || restrictedHosts.length > 0) && (
            <Card>
              <div className="flex flex-wrap items-start gap-3 text-sm">
                <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
                <div className="min-w-0 flex-1">
                  {degradedHosts.length > 0 && (
                    <p className="text-text-dim">
                      <span className="font-medium">{degradedHosts.length}</span> host{degradedHosts.length === 1 ? "" : "s"} could
                      not be checked this pass -- Loki didn&apos;t respond in time, so auth activity there is unknown
                      rather than confirmed clean:{" "}
                      <span className="text-text-faint">{degradedHosts.map((h) => h.hostname).join(", ")}</span>.
                    </p>
                  )}
                  {restrictedHosts.length > 0 && (
                    <p className={degradedHosts.length > 0 ? "mt-1.5 text-text-faint" : "text-text-faint"}>
                      {restrictedHosts.length} host{restrictedHosts.length === 1 ? "" : "s"} restricted to admin accounts for this
                      view.
                    </p>
                  )}
                </div>
              </div>
            </Card>
          )}

          <AuthActivityTable rows={rows} />

          {flaggedHosts.length > 0 && (
            <p className="px-1 text-xs text-text-faint">
              Each row shows a host&apos;s 5 most recent matches -- a host with more shows the true count in its own
              detail sentence rather than here. Use{" "}
              <Link href="/logs" className="inline-flex items-center gap-1 font-medium underline underline-offset-2 hover:text-text-dim">
                the full Logs page
                <ExternalLink className="h-3 w-3" strokeWidth={2} />
              </Link>{" "}
              for anything not covered by the last 60 minutes or the 5-most-recent cap.
            </p>
          )}

          {cleanHosts.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-text-faint">
              <ShieldCheck className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} strokeWidth={2} />
              <span>Also checked and clean: {cleanHosts.map((h) => h.hostname).join(", ")}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
