"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, RefreshCw, AlertTriangle, ShieldOff, Lock, Plus, Minus } from "lucide-react";
import type { AgentSecGroupSignal, AgentSecGroupRule } from "@/lib/types";
import { securityPillTone } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";

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

export default function SecurityGroupDetailPage() {
  const { hostname } = useParams<{ hostname: string }>();
  const { data, error, isLoading, mutate } = useSWR<AgentSecGroupSignal>(
    hostname ? `/api/security/groups/${encodeURIComponent(hostname)}` : null,
    fetcher,
    { refreshInterval: 20000, revalidateOnFocus: true },
  );

  const tone = data ? securityPillTone(data) : null;

  return (
    <div className="space-y-6">
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
            Reading live security-group rules…
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

          <Card padding="p-0">
            <div className="flex items-center gap-2 border-b px-5 py-3" style={{ borderColor: "var(--border-soft)" }}>
              <ShieldOff className="h-4 w-4" style={{ color: "var(--crit)" }} strokeWidth={2} />
              <span className="font-display text-[14px] font-semibold text-color-text">Overly-permissive rules</span>
            </div>
            {(data.risky_rules?.length ?? 0) === 0 ? (
              <div className="p-5 text-sm text-text-faint">None found against the built-in baseline.</div>
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
                {data.risky_rules!.map((r, i) => (
                  <li key={i} className="flex flex-col gap-0.5 px-5 py-3">
                    <span className="text-sm font-medium text-color-text">{r.security_group}</span>
                    <span className="text-xs" style={{ color: "var(--crit)" }}>{r.reason}</span>
                    <span className="text-xs text-text-faint">{formatRule(r.rule)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card padding="p-0">
            <div className="flex items-center gap-2 border-b px-5 py-3" style={{ borderColor: "var(--border-soft)" }}>
              <RefreshCw className="h-4 w-4" style={{ color: "var(--warn)" }} strokeWidth={2} />
              <span className="font-display text-[14px] font-semibold text-color-text">Drift since last snapshot</span>
            </div>
            {(data.drift?.length ?? 0) === 0 ? (
              <div className="p-5 text-sm text-text-faint">
                No change since the last stored snapshot (or no snapshot has been taken for this host yet).
              </div>
            ) : (
              <ul className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
                {data.drift!.map((d, i) => (
                  <li key={i} className="flex flex-col gap-1.5 px-5 py-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-color-text">{d.security_group}</span>
                      <span className="text-xs text-text-faint">since {new Date(d.previous_captured_at).toLocaleString()}</span>
                    </div>
                    {d.added_rules.map((rule, j) => (
                      <div key={`add-${j}`} className="flex items-center gap-1.5 text-xs" style={{ color: "var(--warn)" }}>
                        <Plus className="h-3 w-3 shrink-0" strokeWidth={2.5} />
                        {formatRule(rule)}
                      </div>
                    ))}
                    {d.removed_rules.map((rule, j) => (
                      <div key={`rem-${j}`} className="flex items-center gap-1.5 text-xs text-text-faint">
                        <Minus className="h-3 w-3 shrink-0" strokeWidth={2.5} />
                        {formatRule(rule)}
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
