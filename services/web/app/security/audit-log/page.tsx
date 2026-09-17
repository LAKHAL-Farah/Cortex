"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, AlertTriangle, RefreshCw, FileClock, ShieldCheck, ShieldAlert, Lock } from "lucide-react";
import type { SecurityAuditLogResponse } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityScopeTag from "@/components/SecurityScopeTag";
import { useCurrentUser } from "@/lib/useCurrentUser";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

const HOUR_RANGES = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
];

/** Phase Sec-6: not tied to any single sub-check -- this is the RBAC/
 * redaction story made visible: who asked a security question, what role
 * they had, and whether the answer was redacted, using the same
 * security_rbac.security_agent_involved / filter_security_response_for_
 * role logic already gating chat answers today (see
 * routers/agents.py, services/security_rbac.py). Doubles as the evidence
 * trail Sec 7.11 (Gouvernance & Garde-fous) asks for specifically around
 * security-sensitive answers.
 *
 * Admin-only, same as the backend endpoint (routers/security.py's
 * require_admin) -- this page's whole purpose is letting an admin review
 * *other* accounts' security-flavored turns, including ones that were
 * themselves redacted for a viewer, so a viewer visiting this page sees
 * a plain "you need admin privileges" card rather than a 403 error
 * bubbling up through useSWR (same gating pattern app/admin/page.tsx
 * already uses for /admin).
 *
 * Identity-scoped (SecurityScopeTag): this is keyed by actor/role, not by
 * any one node or instance, even though individual entries can concern
 * either -- same reasoning Keystone tokens uses for the same tag.
 */
export default function AuditLogPage() {
  const { user: me, loading: meLoading } = useCurrentUser();
  const [hours, setHours] = useState(24);

  const { data, error, isLoading, mutate } = useSWR<SecurityAuditLogResponse>(
    me?.role === "admin" ? `/api/security/audit-log?hours=${hours}` : null,
    fetcher,
    { refreshInterval: 30000, revalidateOnFocus: true },
  );

  if (!meLoading && me && me.role !== "admin") {
    return (
      <div className="mx-auto max-w-2xl">
        <Link href="/security" className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
          Back to Security
        </Link>
        <Card className="flex flex-col items-center gap-3 py-12 text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <Lock className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
          </span>
          <div className="font-display text-[17px] font-semibold text-color-text">Security audit log</div>
          <p className="max-w-sm text-sm text-text-faint">
            You need admin privileges to view this page -- it surfaces every account&apos;s security-flavored
            questions, including ones redacted for a viewer at the time.
          </p>
        </Card>
      </div>
    );
  }

  const entries = data?.entries ?? [];
  const redactedCount = entries.filter((e) => e.redacted).length;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">Audit log</h1>
            <SecurityScopeTag scope="identity" />
          </div>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            Who asked a security question, what role they had, and whether the answer they got back was redacted --
            the same RBAC/redaction rule that gates chat answers, replayed over history rather than
            re-implemented. Not a Keystone/Nova/Neutron API-call log; that&apos;s a different, unbuilt thing.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          <div className="inline-flex rounded-[var(--radius-control)] p-0.5" style={{ border: "1px solid var(--border)" }}>
            {HOUR_RANGES.map((r) => (
              <button
                key={r.hours}
                onClick={() => setHours(r.hours)}
                type="button"
                className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
                style={{
                  background: hours === r.hours ? "var(--accent)" : "transparent",
                  color: hours === r.hours ? "#fff" : "var(--text-dim)",
                }}
              >
                {r.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => mutate()}
            type="button"
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-1.5 text-sm font-medium text-text-dim hover:bg-[var(--surface)]"
          >
            <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <Card>
          <div className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--crit)" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
            <span>Audit-log entries are currently unavailable.</span>
            <button onClick={() => mutate()} className="ml-auto inline-flex items-center gap-1.5 font-medium underline underline-offset-2" type="button">
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
              Retry
            </button>
          </div>
        </Card>
      )}

      {meLoading || isLoading ? (
        <Card>
          <div className="flex items-center gap-2 text-sm text-text-faint">
            <RefreshCw className="h-4 w-4 animate-spin" strokeWidth={2} />
            Reading the trace history…
          </div>
        </Card>
      ) : entries.length === 0 ? (
        <Card>
          <div className="flex items-center gap-3 text-sm text-text-faint">
            <FileClock className="h-4 w-4 shrink-0" strokeWidth={2} />
            No security-flavored questions in the last {HOUR_RANGES.find((r) => r.hours === hours)?.label ?? `${hours}h`}.
          </div>
        </Card>
      ) : (
        <>
          <p className="px-1 text-sm text-text-faint">
            <span className="font-medium text-text-dim">{entries.length}</span> security-flavored question
            {entries.length === 1 ? "" : "s"} in the last {HOUR_RANGES.find((r) => r.hours === hours)?.label ?? `${hours}h`}
            {redactedCount > 0 && (
              <>
                {" "}-- <span className="font-medium text-text-dim">{redactedCount}</span> answered with redacted
                detail for the asker&apos;s role.
              </>
            )}
            .
          </p>

          <Card className="!p-0 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-[0.04em] text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
                    <th className="px-4 py-2.5 font-medium">When</th>
                    <th className="px-4 py-2.5 font-medium">Asked by</th>
                    <th className="px-4 py-2.5 font-medium">Role</th>
                    <th className="px-4 py-2.5 font-medium">Question</th>
                    <th className="px-4 py-2.5 font-medium">Agent</th>
                    <th className="px-4 py-2.5 font-medium">Answer</th>
                  </tr>
                </thead>
                <tbody className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
                  {entries.map((e) => (
                    <tr key={e.trace_id}>
                      <td className="whitespace-nowrap px-4 py-2.5 text-text-faint">{new Date(e.created_at).toLocaleString()}</td>
                      <td className="px-4 py-2.5 font-medium text-color-text">{e.username ?? <span className="italic text-text-faint">no caller</span>}</td>
                      <td className="px-4 py-2.5 text-text-faint">{e.user_role ?? "—"}</td>
                      <td className="max-w-md truncate px-4 py-2.5 text-text-dim" title={e.user_query}>{e.user_query}</td>
                      <td className="px-4 py-2.5 text-text-faint">{e.target_agent ?? "—"}</td>
                      <td className="px-4 py-2.5">
                        {e.redacted ? (
                          <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
                            <ShieldAlert className="h-3 w-3" strokeWidth={2} />
                            Redacted
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium" style={{ color: "var(--ok)", background: "var(--ok-soft)" }}>
                            <ShieldCheck className="h-3 w-3" strokeWidth={2} />
                            Full detail
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
