"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowLeft,
  ArrowDown,
  ArrowUp,
  RefreshCw,
  AlertTriangle,
  Lock,
  Plus,
  Minus,
  Layers,
  Info,
  ShieldOff,
} from "lucide-react";
import type { AgentSecGroupSignal, AgentSecGroupRule } from "@/lib/types";
import { securityPillTone, buildGroupInsights } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

function formatPorts(rule: AgentSecGroupRule["rule"]) {
  if (rule.port_range_min == null || rule.port_range_max == null) return "All";
  return rule.port_range_min === rule.port_range_max
    ? String(rule.port_range_min)
    : `${rule.port_range_min}-${rule.port_range_max}`;
}

// Fixed column widths shared by the header row and every rule row so
// everything lines up -- Direction / Protocol / Port / Source / Status,
// same shape a GitHub or Notion table would use for structured data
// instead of one free-text "Rule" cell.
const RULE_GRID_COLS = "grid-cols-[92px_84px_108px_1fr_104px]";

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
              <span className="inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium" style={{ color: tone.color, background: tone.soft }}>
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
              One table per security group in use by an instance actually hosted on this node right now (via
              Nova&apos;s <code>hypervisor_hostname</code>) -- OpenStack attaches security groups to an instance&apos;s
              own port, never to the compute node itself, so this is a union across every VM scheduled here, not a
              property of the hardware. A rule is tagged{" "}
              <strong>Risky</strong> and called out below it if it trips the built-in overly-permissive baseline,
              and <strong>Added</strong>/<strong>Removed</strong> if it changed since the last stored snapshot -- a
              periodic job that runs every SECURITY_SNAPSHOT_INTERVAL_SECONDS, so a rule can be flagged as drift
              even when it isn&apos;t itself risky.
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
                  <div key={insight.group.id} className="panel flex flex-col gap-3 p-5">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <span className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
                          <Layers className="h-4 w-4" style={{ color: groupTone }} strokeWidth={1.75} />
                        </span>
                        <div>
                          <div className="font-display text-[15px] font-semibold text-color-text">{insight.group.name}</div>
                          <div className="text-xs text-text-faint">
                            {insight.group.rules.length} rule{insight.group.rules.length === 1 ? "" : "s"}
                          </div>
                        </div>
                      </div>
                      <span
                        className="inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-[11px] font-medium"
                        style={{ color: groupTone, background: insight.isFlagged ? "var(--crit-soft)" : "var(--ok-soft)" }}
                      >
                        {insight.isFlagged ? "Flagged" : "Clean"}
                      </span>
                    </div>

                    {insight.rules.length === 0 && insight.removedRules.length === 0 ? (
                      <p className="text-sm text-text-faint">No rules on this group.</p>
                    ) : (
                      <div className="overflow-hidden rounded-[var(--radius-control)] border" style={{ borderColor: "var(--border-soft)" }}>
                        <div
                          className={`grid ${RULE_GRID_COLS} gap-2 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.07em] text-text-muted`}
                          style={{ background: "var(--canvas)" }}
                        >
                          <div>Direction</div>
                          <div>Protocol</div>
                          <div>Port</div>
                          <div>Source</div>
                          <div className="text-right">Status</div>
                        </div>

                        <div>
                          {insight.rules.map((ar, i) => {
                            const r = ar.rule;
                            const isIngress = r.direction === "ingress";
                            const isRisky = Boolean(ar.riskyReason);
                            const cellColor = isRisky ? "var(--crit)" : "var(--text-dim)";
                            return (
                              <div key={i}>
                                <div
                                  className={`grid ${RULE_GRID_COLS} items-center gap-2 px-3 py-2.5 text-xs`}
                                  style={{
                                    borderTop: "1px solid var(--border-soft)",
                                    background: isRisky ? "var(--crit-soft)" : "transparent",
                                    boxShadow: isRisky ? "inset 3px 0 0 0 var(--crit)" : "none",
                                  }}
                                >
                                  <div className="flex items-center gap-1.5 font-medium" style={{ color: cellColor }}>
                                    {isIngress ? (
                                      <ArrowDown className="h-3 w-3 shrink-0" strokeWidth={2} />
                                    ) : (
                                      <ArrowUp className="h-3 w-3 shrink-0" strokeWidth={2} />
                                    )}
                                    {isIngress ? "Ingress" : "Egress"}
                                  </div>
                                  <div className="uppercase" style={{ color: cellColor, fontFamily: "var(--font-mono)" }}>
                                    {r.protocol ?? "All"}
                                  </div>
                                  <div style={{ color: cellColor, fontFamily: "var(--font-mono)" }}>{formatPorts(r)}</div>
                                  <div className="truncate" style={{ color: cellColor, fontFamily: "var(--font-mono)" }} title={r.remote_ip_prefix ?? "0.0.0.0/0"}>
                                    {r.remote_ip_prefix ?? "Any"}
                                  </div>
                                  <div className="flex flex-wrap items-center justify-end gap-1">
                                    {isRisky && (
                                      <span
                                        className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold"
                                        style={{ color: "#fff", background: "var(--crit)" }}
                                      >
                                        <ShieldOff className="h-2.5 w-2.5" strokeWidth={2.5} />
                                        Risky
                                      </span>
                                    )}
                                    {ar.driftStatus === "added" && (
                                      <span
                                        className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold"
                                        style={{ color: "var(--warn)", background: "var(--warn-soft)" }}
                                      >
                                        <Plus className="h-2.5 w-2.5" strokeWidth={2.5} />
                                        Added
                                      </span>
                                    )}
                                    {!isRisky && ar.driftStatus !== "added" && <span className="text-[10px] text-text-faint">—</span>}
                                  </div>
                                </div>
                                {isRisky && (
                                  <div
                                    className="flex items-start gap-2 px-3 py-2 text-xs font-semibold"
                                    style={{ background: "var(--crit-soft)", color: "var(--crit)", boxShadow: "inset 3px 0 0 0 var(--crit)" }}
                                  >
                                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2.25} />
                                    {ar.riskyReason}
                                  </div>
                                )}
                              </div>
                            );
                          })}

                          {insight.removedRules.map((rule, i) => {
                            const isIngress = rule.direction === "ingress";
                            return (
                              <div
                                key={`removed-${i}`}
                                className={`grid ${RULE_GRID_COLS} items-center gap-2 px-3 py-2.5 text-xs`}
                                style={{ borderTop: "1px solid var(--border-soft)" }}
                              >
                                <div className="flex items-center gap-1.5 text-text-faint line-through decoration-text-faint/60">
                                  {isIngress ? (
                                    <ArrowDown className="h-3 w-3 shrink-0" strokeWidth={2} />
                                  ) : (
                                    <ArrowUp className="h-3 w-3 shrink-0" strokeWidth={2} />
                                  )}
                                  {isIngress ? "Ingress" : "Egress"}
                                </div>
                                <div className="uppercase text-text-faint line-through decoration-text-faint/60" style={{ fontFamily: "var(--font-mono)" }}>
                                  {rule.protocol ?? "All"}
                                </div>
                                <div className="text-text-faint line-through decoration-text-faint/60" style={{ fontFamily: "var(--font-mono)" }}>
                                  {formatPorts(rule)}
                                </div>
                                <div className="truncate text-text-faint line-through decoration-text-faint/60" style={{ fontFamily: "var(--font-mono)" }}>
                                  {rule.remote_ip_prefix ?? "Any"}
                                </div>
                                <div className="flex items-center justify-end">
                                  <span
                                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold"
                                    style={{ color: "var(--text-faint)", background: "var(--canvas)" }}
                                  >
                                    <Minus className="h-2.5 w-2.5" strokeWidth={2.5} />
                                    Removed
                                  </span>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
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
        </>
      )}
    </div>
  );
}
