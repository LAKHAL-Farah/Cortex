"use client";

import { useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ShieldAlert,
  Terminal,
  Network as NetworkIcon,
  ScrollText,
  Cpu,
  Globe,
  KeyRound,
  FileClock,
  RefreshCw,
  AlertTriangle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SecurityFinding, AgentSecuritySignal } from "@/lib/types";
import { securityPillTone } from "@/lib/securityStatus";
import { Card } from "@/components/ui/Card";
import MetricCard from "@/components/ui/MetricCard";

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
    { refreshInterval: 20000, revalidateOnFocus: true },
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
            first.
          </p>
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

      <Card padding="p-0">
        <div className="flex items-center gap-2 border-b px-5 py-3" style={{ borderColor: "var(--border-soft)" }}>
          <ShieldAlert className="h-4 w-4" style={{ color: "var(--accent)" }} strokeWidth={2} />
          <span className="font-display text-[14px] font-semibold text-color-text">Per-host posture</span>
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
                  <th className="px-3 py-2 font-medium">Auth</th>
                  <th className="px-3 py-2 font-medium">Security groups</th>
                  <th className="px-3 py-2 font-medium">CVEs</th>
                  <th className="px-3 py-2 font-medium">eBPF</th>
                </tr>
              </thead>
              <tbody>
                {findings.map((f) => (
                  <tr key={f.hostname} className="border-b last:border-0" style={{ borderColor: "var(--border-soft)" }}>
                    <td className="px-5 py-2.5">
                      <Link href={`/security/security-groups/${encodeURIComponent(f.hostname)}`} className="font-medium text-color-text hover:underline">
                        {f.hostname}
                      </Link>
                      <span className="ml-2 text-xs text-text-faint">{f.role}</span>
                    </td>
                    <td className="px-3 py-2.5"><StatusPill signal={f.raw_data.auth_signal} /></td>
                    <td className="px-3 py-2.5"><StatusPill signal={f.raw_data.sec_group_signal} /></td>
                    <td className="px-3 py-2.5"><StatusPill signal={f.raw_data.cve_signal} /></td>
                    <td className="px-3 py-2.5"><StatusPill signal={f.raw_data.ebpf_signal} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div>
        <div className="eyebrow mb-2 px-1">Browse by category</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {CATEGORIES.map((cat) => (
            <Link key={cat.href} href={cat.href} className="block">
              <Card interactive padding="p-0" className="relative overflow-hidden">
                <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: cat.color }} />
                <div className="flex items-center justify-between gap-3 p-4 pl-6">
                  <div className="flex items-center gap-3">
                    <span
                      className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
                      style={{ background: `color-mix(in srgb, ${cat.color} 14%, transparent)` }}
                    >
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
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
