"use client";

import { useMemo } from "react";
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
import type { SecurityFinding } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import MetricCard from "@/components/ui/MetricCard";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";
import SecurityPostureTable from "@/components/SecurityPostureTable";
import SecurityCategoryCard, { type SecurityCategory } from "@/components/SecurityCategoryCard";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

// Sub-pages this overview links to -- only "Security groups" has a real
// page behind it (Phase Sec-1's stored-snapshot diff); the rest route to
// a plain "not available yet" placeholder rather than pretending there's
// a built page (or worse, real data) behind them. See each page's own
// ComingSoon `blockedOn` text for exactly what's missing. `description`
// is what the integration-style card in "Browse by category" shows under
// the title -- one line on what that category actually checks.
const CATEGORIES: SecurityCategory[] = [
  {
    label: "Auth activity",
    description: "SSH and login anomalies pulled straight from each host's auth log, via Loki.",
    href: "/security/auth-activity",
    icon: Terminal,
    color: "var(--chart-1)",
    available: false,
  },
  {
    label: "Security groups",
    description: "Neutron ingress/egress rules audited against the risky baseline, plus drift since the last snapshot.",
    href: "/security/security-groups",
    icon: NetworkIcon,
    color: "var(--chart-3)",
    available: true,
  },
  {
    label: "Vulnerabilities (CVE)",
    description: "Installed package versions on every monitored node, matched against known CVEs.",
    href: "/security/vulnerabilities",
    icon: ScrollText,
    color: "var(--chart-4)",
    available: false,
  },
  {
    label: "Kernel signals (eBPF)",
    description: "Falco alerts for suspicious syscalls, capability use, and container escapes.",
    href: "/security/kernel-signals",
    icon: Cpu,
    color: "var(--chart-5)",
    available: false,
  },
  {
    label: "Exposed ports",
    description: "Every listening port per host, flagged when it's reachable from outside its security group.",
    href: "/security/exposed-ports",
    icon: Globe,
    color: "var(--chart-2)",
    available: false,
  },
  {
    label: "Keystone tokens",
    description: "Token issuance and reuse patterns across every OpenStack service call.",
    href: "/security/keystone-tokens",
    icon: KeyRound,
    color: "var(--chart-1)",
    available: false,
  },
  {
    label: "Audit log",
    description: "Every Keystone/Nova/Neutron API call, searchable by actor, project, and action.",
    href: "/security/audit-log",
    icon: FileClock,
    color: "var(--chart-2)",
    available: false,
  },
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
          <SecurityPostureTable findings={findings} />
        )}
      </div>

      <div>
        <div className="eyebrow mb-2 px-1">Browse by category</div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CATEGORIES.map((cat) => (
            <SecurityCategoryCard key={cat.href} category={cat} />
          ))}
        </div>
      </div>
    </div>
  );
}
