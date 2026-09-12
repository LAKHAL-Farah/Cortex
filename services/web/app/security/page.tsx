"use client";

import { useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ShieldAlert,
  Shield,
  Terminal,
  Network as NetworkIcon,
  ScrollText,
  Cpu,
  Globe,
  KeyRound,
  FileClock,
  RefreshCw,
  AlertTriangle,
  ChevronRight,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SecurityFinding, AgentSecuritySignal } from "@/lib/types";
import { securityPillTone, overallSecurityTone } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";
import MetricCard from "@/components/ui/MetricCard";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

function StatusPill({ signal }: { signal: AgentSecuritySignal }) {
  const tone = securityPillTone(signal);
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ color: tone.color, background: tone.soft }}
    >
      {tone.label}
    </span>
  );
}

// One sub-check row inside a host's big card -- icon + label on the left,
// StatusPill on the right, same four sub-checks the old table's columns
// were, just laid out for a card instead of a table cell.
const SIGNAL_ROWS: { key: "auth_signal" | "sec_group_signal" | "cve_signal" | "ebpf_signal"; label: string; icon: LucideIcon }[] = [
  { key: "auth_signal", label: "Auth activity", icon: Terminal },
  { key: "sec_group_signal", label: "Security groups", icon: NetworkIcon },
  { key: "cve_signal", label: "Known CVEs", icon: ScrollText },
  { key: "ebpf_signal", label: "Kernel signals", icon: Cpu },
];

// Sub-pages this overview links to -- only "Security groups" has a real
// page behind it (Phase Sec-1's stored-snapshot diff); the rest route to
// a plain "not available yet" placeholder rather than pretending there's
// a built page (or worse, real data) behind them. See each page's own
// ComingSoon `blockedOn` text for exactly what's missing.
const CATEGORIES: { label: string; href: string; icon: LucideIcon; color: string; available: boolean }[] = [
  { label: "Auth activity", href: "/security/auth-activity", icon: Terminal, color: "var(--chart-1)", available: false },
  { label: "Security groups", href: "/security/security-groups", icon: NetworkIcon, color: "var(--chart-3)", available: true },
  { label: "Vulnerabilities (CVE)", href: "/security/vulnerabilities", icon: ScrollText, color: "var(--chart-4)", available: false },
  { label: "Kernel signals (eBPF)", href: "/security/kernel-signals", icon: Cpu, color: "var(--chart-5)", available: false },
  { label: "Exposed ports", href: "/security/exposed-ports", icon: Globe, color: "var(--chart-2)", available: false },
  { label: "Keystone tokens", href: "/security/keystone-tokens", icon: KeyRound, color: "var(--chart-1)", available: false },
  { label: "Audit log", href: "/security/audit-log", icon: FileClock, color: "var(--chart-2)", available: false },
];

export default function SecurityOverviewPage() {
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    // Phase Sec-3: /findings now reads a cache the backend refreshes on
    // its own schedule (SECURITY_SCAN_INTERVAL_SECONDS, default 45s) --
    // a plain indexed Postgres read, not a live per-request scan -- so
    // this can poll noticeably tighter than it used to without adding
    // real load. SecurityRescanButton below covers "I don't want to
    // wait even that long" with an explicit on-demand trigger.
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const findings = useMemo(() => data?.findings ?? [], [data]);

  // Fleet-wide summary strip -- counted from has_signal booleans, which
  // survive RBAC redaction for every role (see services/security_rbac.py),
  // so this strip means the same thing for an admin and a viewer even
  // though the per-host table below shows a viewer less detail.
  const summary = useMemo(() => {
    let authFlags = 0, secGroupFlags = 0, cveFlags = 0, ebpfFlags = 0;
    for (const f of findings) {
      if (f.raw_data.auth_signal.has_signal) authFlags += 1;
      if (f.raw_data.sec_group_signal.has_signal) secGroupFlags += 1;
      if (f.raw_data.cve_signal.has_signal) cveFlags += 1;
      if (f.raw_data.ebpf_signal.has_signal) ebpfFlags += 1;
    }
    return { authFlags, secGroupFlags, cveFlags, ebpfFlags };
  }, [findings]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-[22px] font-semibold text-color-text">Security</h1>
          <p className="mt-1 text-sm text-text-faint">
            The Security Agent&apos;s four sub-checks (auth activity, security groups, known CVEs, kernel-level
            signals), polled directly -- the same findings the Copilot gives you in chat, without asking a question
            first. Refreshes automatically as the scan pass below completes; use Rescan to trigger one right now.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <SecurityHealthBadge />
          <SecurityRescanButton />
          <button
            onClick={() => mutate()}
            type="button"
            title="Re-fetch the current cache without triggering a new scan pass"
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
            <span>Security findings are currently unavailable.</span>
            <button onClick={() => mutate()} className="ml-auto inline-flex items-center gap-1.5 font-medium underline underline-offset-2" type="button">
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />
              Retry
            </button>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard title="Auth activity flagged" value={isLoading ? "…" : summary.authFlags} unit="hosts" icon={Terminal} iconColor="var(--chart-1)" />
        <MetricCard title="Security groups flagged" value={isLoading ? "…" : summary.secGroupFlags} unit="hosts" icon={NetworkIcon} iconColor="var(--chart-3)" />
        <MetricCard title="Known CVEs flagged" value={isLoading ? "…" : summary.cveFlags} unit="hosts" icon={ScrollText} iconColor="var(--chart-4)" />
        <MetricCard title="Kernel-level alerts" value={isLoading ? "…" : summary.ebpfFlags} unit="hosts" icon={Cpu} iconColor="var(--chart-5)" />
      </div>

      <div>
        <div className="mb-2 flex items-center gap-2 px-1">
          <ShieldAlert className="h-4 w-4" style={{ color: "var(--accent)" }} strokeWidth={2} />
          <span className="font-display text-[14px] font-semibold text-color-text">Per-host posture</span>
        </div>
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {findings.map((f) => {
              const tone = overallSecurityTone(f.raw_data);
              return (
                <Link key={f.hostname} href={`/security/security-groups/${encodeURIComponent(f.hostname)}`} className="block">
                  <div
                    className="tile-card tile-card--interactive flex h-full flex-col gap-4 p-5"
                    style={{ ["--tile-color" as string]: tone.color }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <span className="tile-icon h-11 w-11">
                          <Shield className="h-5 w-5" style={{ color: tone.color }} strokeWidth={2} />
                        </span>
                        <div>
                          <div className="font-display text-[15px] font-semibold text-color-text">{f.hostname}</div>
                          <div className="text-xs text-text-faint">{f.role}</div>
                        </div>
                      </div>
                      <ChevronRight className="mt-1.5 h-4 w-4 shrink-0 text-text-faint" strokeWidth={2} />
                    </div>
                    <div className="grid grid-cols-1 gap-2 border-t pt-3" style={{ borderColor: "var(--border-soft)" }}>
                      {SIGNAL_ROWS.map((row) => (
                        <div key={row.key} className="flex items-center justify-between gap-2">
                          <span className="flex items-center gap-2 text-xs text-text-dim">
                            <row.icon className="h-3.5 w-3.5" strokeWidth={2} />
                            {row.label}
                          </span>
                          <StatusPill signal={f.raw_data[row.key]} />
                        </div>
                      ))}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <div className="eyebrow mb-2 px-1">Browse by category</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {CATEGORIES.map((cat) => (
            <Link key={cat.href} href={cat.href} className="block">
              <div
                className="tile-card tile-card--interactive flex items-center justify-between gap-3 p-4"
                style={{ ["--tile-color" as string]: cat.color }}
              >
                <div className="flex items-center gap-3">
                  <span className="tile-icon h-9 w-9">
                    <cat.icon className="h-4.5 w-4.5" style={{ color: cat.color }} strokeWidth={2} />
                  </span>
                  <span className="font-display text-[14px] font-medium text-color-text">{cat.label}</span>
                </div>
                {!cat.available && (
                  <span className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium text-text-faint" style={{ background: "var(--canvas)" }}>
                    Coming soon
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
