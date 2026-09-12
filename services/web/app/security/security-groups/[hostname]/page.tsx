"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowLeft,
  RefreshCw,
  AlertTriangle,
  Lock,
  Plus,
  Minus,
  Layers,
  Info,
  FlaskConical,
  Loader2,
  ShieldOff,
} from "lucide-react";
import type { AgentSecGroupSignal, AgentSecGroupRule, SecurityStatus } from "@/lib/types";
import { securityPillTone, buildGroupInsights } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

function formatRule(rule: AgentSecGroupRule["rule"]) {
  const proto = rule.protocol ?? "all protocols";
  const ports =
    rule.port_range_min != null && rule.port_range_max != null
      ? rule.port_range_min === rule.port_range_max
        ? `port ${rule.port_range_min}`
        : `ports ${rule.port_range_min}-${rule.port_range_max}`
      : "all ports";
  const from = rule.remote_ip_prefix ?? "any source";
  return `${rule.direction} · ${proto} · ${ports} · ${from}`;
}

/** Sandbox-only "simulate a real drift" control (Phase Sec-3/4). Only
 * ever rendered when GET /api/security/status' `sandbox_mode` is true
 * (CORTEX_ENV=sandbox on the API side, see services/security_sandbox.py)
 * -- the backend endpoints this calls 404 outside the sandbox regardless,
 * this just avoids showing a button that can't work in a real deployment.
 */
function SandboxDriftDemo({ hostname, onChanged }: { hostname: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [injected, setInjected] = useState<{ ruleId: string; detail: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const inject = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/security/sandbox/drift/${encodeURIComponent(hostname)}`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(body.detail ?? `Request failed (${res.status}).`);
      } else {
        setInjected({ ruleId: body.rule_id, detail: body.detail });
        onChanged();
      }
    } catch {
      setError("Could not reach the sandbox drift endpoint.");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!injected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/security/sandbox/drift/${encodeURIComponent(hostname)}?rule_id=${encodeURIComponent(injected.ruleId)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? `Request failed (${res.status}).`);
      } else {
        setInjected(null);
        onChanged();
      }
    } catch {
      setError("Could not reach the sandbox drift endpoint.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tile-card p-5" style={{ ["--tile-color" as string]: "var(--warn)", borderStyle: "dashed" }}>
      <div className="flex items-center gap-2">
        <span className="tile-icon h-9 w-9">
          <FlaskConical className="h-4 w-4" style={{ color: "var(--warn)" }} strokeWidth={2} />
        </span>
        <span className="font-display text-[14px] font-semibold text-color-text">Simulate drift</span>
        <span className="ml-auto rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
          Sandbox only
        </span>
      </div>
      <p className="mt-3 text-sm text-text-faint">
        Takes a fresh baseline snapshot of this host right now, then adds one real ingress rule (an internal-only
        port, via openstack-sim&apos;s own fault-injection endpoint) to whichever security group is actually
        attached here. Nothing is faked in the UI -- the rule really is added to the simulated OpenStack backend,
        so it shows up below, on the group it was added to, the same way a real configuration change would.
      </p>
      {error && (
        <p className="mt-2 text-xs" style={{ color: "var(--crit)" }}>
          {error}
        </p>
      )}
      <div className="mt-3">
        {injected ? (
          <>
            <p className="mb-2 text-xs text-text-dim">{injected.detail}</p>
            <button
              onClick={reset}
              disabled={busy}
              type="button"
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-1.5 text-sm font-medium disabled:opacity-60"
              style={{ border: "1px solid var(--border)", color: "var(--text)" }}
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> : <Minus className="h-3.5 w-3.5" strokeWidth={2} />}
              Remove the injected rule
            </button>
          </>
        ) : (
          <button
            onClick={inject}
            disabled={busy}
            type="button"
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-1.5 text-sm font-medium disabled:opacity-60"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> : <Plus className="h-3.5 w-3.5" strokeWidth={2} />}
            Inject a real drift on {hostname}
          </button>
        )}
      </div>
    </div>
  );
}

export default function SecurityGroupDetailPage() {
  const { hostname } = useParams<{ hostname: string }>();
  const { data, error, isLoading, mutate } = useSWR<AgentSecGroupSignal>(
    hostname ? `/api/security/groups/${encodeURIComponent(hostname)}` : null,
    fetcher,
    // Phase Sec-3: this now reads security_finding_cache (a plain
    // Postgres lookup) rather than computing rules/drift live on every
    // request, so a tighter poll doesn't add real backend load.
    { refreshInterval: 15000, revalidateOnFocus: true },
  );
  const { data: status } = useSWR<SecurityStatus>("/api/security/status", fetcher, { refreshInterval: 15000 });

  const tone = data ? securityPillTone(data) : null;
  const insights = data ? buildGroupInsights(data) : [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security/security-groups" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security groups
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">{hostname}</h1>
            {tone && (
              <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium" style={{ color: tone.color, background: tone.soft }}>
                {tone.label}
              </span>
            )}
          </div>
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
            <span>{(error as Error).message.includes("404") ? `No known node "${hostname}".` : "This host's security-group data is currently unavailable."}</span>
            <button onClick={() => mutate()} className="ml-auto inline-flex items-center gap-1.5 font-medium underline underline-offset-2" type="button">
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
              Retry
            </button>
          </div>
        </Card>
      )}

      {isLoading && (
        <Card>
          <div className="flex items-center gap-2 text-sm text-text-faint">
            <RefreshCw className="h-4 w-4 animate-spin" strokeWidth={2} />
            Reading cached security-group rules…
          </div>
        </Card>
      )}

      {data?.restricted && (
        <Card className="flex flex-col items-center gap-3 py-10 text-center">
          <Lock className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
          <p className="max-w-sm text-sm text-text-faint">
            Security-group rule and drift details are restricted to admin accounts. Ask an admin to review this
            host, or sign in with an admin account to see the full finding.
          </p>
        </Card>
      )}

      {data && !data.restricted && (
        <>
          {data.detail && (
            <Card>
              <p className="text-sm text-text-dim">{data.detail}</p>
            </Card>
          )}

          <div className="flex items-start gap-2 rounded-[var(--radius-panel)] border px-4 py-3 text-xs text-text-faint" style={{ borderColor: "var(--border-soft)", background: "var(--surface)" }}>
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2} />
            <span>
              One card per security group actually attached to this host right now. Every rule the group holds is
              listed; a rule gets a red <strong>Risky</strong> tag if it trips the built-in overly-permissive
              baseline (with the specific reason), and an amber <strong>Added</strong>/<strong>Removed</strong> tag
              if it changed since the last stored snapshot -- a periodic job that runs every
              SECURITY_SNAPSHOT_INTERVAL_SECONDS, so a rule can be flagged as drift even when it isn&apos;t itself
              risky.
            </span>
          </div>

          {insights.length === 0 ? (
            <Card>
              <div className="text-sm text-text-faint">No security group attached to any instance on this host.</div>
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {insights.map((insight) => {
                const groupTone = insight.isFlagged ? "var(--crit)" : "var(--ok)";
                return (
                  <div
                    key={insight.group.id}
                    className="tile-card flex flex-col gap-3 p-5"
                    style={{ ["--tile-color" as string]: groupTone }}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <span className="tile-icon h-10 w-10">
                          <Layers className="h-4.5 w-4.5" style={{ color: groupTone }} strokeWidth={2} />
                        </span>
                        <div>
                          <div className="font-display text-[15px] font-semibold text-color-text">{insight.group.name}</div>
                          <div className="text-xs text-text-faint">
                            {insight.group.rules.length} rule{insight.group.rules.length === 1 ? "" : "s"}
                          </div>
                        </div>
                      </div>
                      <span
                        className="inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium"
                        style={{ color: groupTone, background: insight.isFlagged ? "var(--crit-soft)" : "var(--ok-soft)" }}
                      >
                        {insight.isFlagged ? "Flagged" : "Clean"}
                      </span>
                    </div>

                    {insight.rules.length === 0 && insight.removedRules.length === 0 ? (
                      <p className="text-sm text-text-faint">No rules on this group.</p>
                    ) : (
                      <ul className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
                        {insight.rules.map((ar, i) => (
                          <li key={i} className="flex flex-col gap-1 py-2">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="text-xs text-text-dim">{formatRule(ar.rule)}</span>
                              {ar.riskyReason && (
                                <span
                                  className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                                  style={{ color: "var(--crit)", background: "var(--crit-soft)" }}
                                  title={ar.riskyReason}
                                >
                                  <ShieldOff className="h-2.5 w-2.5" strokeWidth={2.5} />
                                  Risky
                                </span>
                              )}
                              {ar.driftStatus === "added" && (
                                <span
                                  className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                                  style={{ color: "var(--warn)", background: "var(--warn-soft)" }}
                                >
                                  <Plus className="h-2.5 w-2.5" strokeWidth={2.5} />
                                  Added
                                </span>
                              )}
                            </div>
                            {ar.riskyReason && (
                              <span className="pl-0 text-[11px]" style={{ color: "var(--crit)" }}>
                                {ar.riskyReason}
                              </span>
                            )}
                          </li>
                        ))}
                        {insight.removedRules.map((rule, i) => (
                          <li key={`removed-${i}`} className="flex items-center gap-1.5 py-2">
                            <span
                              className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                              style={{ color: "var(--text-faint)", background: "var(--canvas)" }}
                            >
                              <Minus className="h-2.5 w-2.5" strokeWidth={2.5} />
                              Removed
                            </span>
                            <span className="text-xs text-text-faint line-through decoration-text-faint/60">{formatRule(rule)}</span>
                          </li>
                        ))}
                      </ul>
                    )}

                    {insight.driftSince && (
                      <p className="text-[11px] text-text-faint">
                        Compared against the snapshot taken {new Date(insight.driftSince).toLocaleString()}.
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {status?.sandbox_mode && hostname && <SandboxDriftDemo hostname={hostname} onChanged={() => mutate()} />}
        </>
      )}
    </div>
  );
}
