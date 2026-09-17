"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, AlertTriangle, RefreshCw, KeyRound, ShieldQuestion, ShieldCheck, ShieldAlert, Repeat, MapPinOff, Timer } from "lucide-react";
import type { KeystoneTokenSignal } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityScopeTag from "@/components/SecurityScopeTag";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

function ttlLabel(seconds: number): string {
  const hours = seconds / 3600;
  return hours >= 1 ? `${hours.toFixed(1)}h` : `${Math.round(seconds / 60)}m`;
}

/** Phase Sec-5c: Identity scope, fleet/project-wide -- unlike every other
 * Security sub-page, this has no per-host table at all (see
 * services/keystone_audit.py's own module docstring for why forcing a
 * fleet-wide finding into a per-host shape would misrepresent what it
 * is). GET /api/security/keystone-tokens hits routers/security.py's own
 * dedicated, live endpoint -- there's no per-node `security_finding_
 * cache` row this could ride, since a token-issuance pattern isn't
 * scoped to any one node.
 */
export default function KeystoneTokensPage() {
  const { data, error, isLoading, mutate } = useSWR<KeystoneTokenSignal>(
    "/api/security/keystone-tokens",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">Keystone tokens</h1>
            <SecurityScopeTag scope="identity" />
          </div>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            Token-issuance patterns across the whole fleet, not any one host: the same user re-issuing tokens
            unusually fast, a token issued from an IP outside the configured expected range, or a token alive far
            longer than a sane session TTL. A small, explicit rule-set -- not an ML model.
          </p>
        </div>
      </div>

      {error && (
        <Card>
          <div className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--crit)" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
            <span>Keystone token findings are currently unavailable.</span>
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
            Reading the token-issuance log…
          </div>
        </Card>
      ) : data ? (
        <KeystoneTokenView signal={data} />
      ) : null}
    </div>
  );
}

function KeystoneTokenView({ signal }: { signal: KeystoneTokenSignal }) {
  if (signal.restricted) {
    return (
      <Card>
        <div className="flex items-center gap-3 text-sm">
          <ShieldQuestion className="h-4 w-4 shrink-0 text-text-faint" strokeWidth={2} />
          <span className="text-text-faint">
            {signal.has_signal ? "A token-abuse pattern was flagged this pass" : "No token-abuse pattern this pass"} --
            details restricted to admin accounts.
          </span>
        </div>
      </Card>
    );
  }

  if (signal.degraded) {
    return (
      <Card>
        <div className="flex items-center gap-3 text-sm" style={{ color: "var(--warn)" }}>
          <ShieldQuestion className="h-4 w-4 shrink-0" strokeWidth={2} />
          {signal.detail ?? "Couldn't reach the Keystone token-issuance log -- no collector deployed yet."}
        </div>
      </Card>
    );
  }

  if (!signal.has_signal) {
    return (
      <Card>
        <div className="flex items-center gap-3 text-sm" style={{ color: "var(--ok)" }}>
          <ShieldCheck className="h-4 w-4 shrink-0" strokeWidth={2} />
          {signal.detail ?? `No token-abuse pattern found across ${signal.event_count ?? 0} issuance event(s).`}
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-center gap-3 text-sm font-medium" style={{ color: "var(--crit)" }}>
          <KeyRound className="h-4 w-4 shrink-0" strokeWidth={2} />
          {signal.detail}
        </div>
      </Card>

      {(signal.rapid_reissue?.length ?? 0) > 0 && (
        <PatternCard
          icon={Repeat}
          title="Rapid re-issuance"
          description="The same user issued several tokens in a very short window -- a real operator re-authenticating that often is unusual; a compromised credential re-issuing per request looks exactly like this."
        >
          <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
            {signal.rapid_reissue!.map((r, i) => (
              <div key={i} className="flex items-center justify-between py-2 text-sm">
                <span className="font-medium text-color-text">{r.username}</span>
                <span className="text-text-faint">
                  {r.count} tokens in {r.window_seconds}s, starting {new Date(r.first_issued_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </PatternCard>
      )}

      {signal.unexpected_ip_checked && (signal.unexpected_ip?.length ?? 0) > 0 && (
        <PatternCard
          icon={MapPinOff}
          title="Unexpected source IP"
          description="A token was issued from an IP outside the configured expected range (CORTEX_KEYSTONE_EXPECTED_CIDRS)."
        >
          <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
            {signal.unexpected_ip!.map((r, i) => (
              <div key={i} className="flex items-center justify-between py-2 text-sm">
                <span className="font-medium text-color-text">{r.username ?? "unknown user"}</span>
                <span className="font-mono text-text-faint">{r.source_ip}</span>
              </div>
            ))}
          </div>
        </PatternCard>
      )}

      {(signal.long_lived?.length ?? 0) > 0 && (
        <PatternCard
          icon={Timer}
          title="Long-lived tokens"
          description="A token alive far longer than a sane session TTL -- independent of who holds it or where it was issued from."
        >
          <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
            {signal.long_lived!.map((r, i) => (
              <div key={i} className="flex items-center justify-between py-2 text-sm">
                <span className="font-medium text-color-text">{r.username ?? "unknown user"}</span>
                <span className="text-text-faint">alive {ttlLabel(r.ttl_seconds)}</span>
              </div>
            ))}
          </div>
        </PatternCard>
      )}

      {!signal.unexpected_ip_checked && (
        <div className="flex items-center gap-2 px-1 text-xs text-text-faint">
          <ShieldAlert className="h-3.5 w-3.5" strokeWidth={2} />
          Unexpected-source-IP checking is off -- set CORTEX_KEYSTONE_EXPECTED_CIDRS to enable it.
        </div>
      )}
    </div>
  );
}

function PatternCard({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: typeof Repeat;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-[var(--radius-control)]" style={{ background: "var(--crit-soft)" }}>
          <Icon className="h-4.5 w-4.5" style={{ color: "var(--crit)" }} strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-display text-[14px] font-semibold text-color-text">{title}</div>
          <p className="mt-0.5 text-xs text-text-faint">{description}</p>
          <div className="mt-2">{children}</div>
        </div>
      </div>
    </Card>
  );
}
