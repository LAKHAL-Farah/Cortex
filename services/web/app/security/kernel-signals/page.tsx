"use client";

import { useMemo, useState, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, Cpu, AlertTriangle, RefreshCw, ShieldQuestion, ShieldCheck } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";
import SecurityScopeTag from "@/components/SecurityScopeTag";
import SecurityHostFilterBanner from "@/components/SecurityHostFilterBanner";
import EbpfAlertsTable from "@/components/EbpfAlertsTable";
import { EBPF_PRIORITY_ORDER, buildEbpfHostStatuses, ebpfPriorityTone, filterFindingsByHost, flattenEbpfFindings, type KnownEbpfPriority } from "@/lib/securityStatus";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

type PriorityFilter = "all" | KnownEbpfPriority;

/** Clickable priority count chip -- same shape/behavior as the
 * vulnerabilities page's own SeverityStat (toggle the filter on click,
 * click again to clear). Defined locally for the same reason that one
 * is: it isn't exported, and this page's filter set (eBPF priorities) is
 * different from CVE severities. */
function PriorityStat({
  priority,
  count,
  active,
  onClick,
}: {
  priority: KnownEbpfPriority;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  const tone = ebpfPriorityTone(priority);
  return (
    <button
      onClick={onClick}
      type="button"
      className="panel panel-interactive flex items-center gap-3 p-4 text-left transition-shadow"
      style={active ? { borderColor: tone.color, boxShadow: `0 0 0 1px ${tone.color}` } : undefined}
    >
      <div
        className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
        style={{ background: tone.soft }}
      >
        <Cpu className="h-4.5 w-4.5" style={{ color: tone.color }} strokeWidth={1.75} />
      </div>
      <div className="min-w-0">
        <div className="stat-figure text-xl text-color-text">{count}</div>
        <div className="truncate text-xs text-text-faint">{tone.label}</div>
      </div>
    </button>
  );
}

/** Phase Sec-4: real kernel-level (eBPF) alert data, unblocked by
 * tetragon-bridge-sim in the sandbox (infra/tetragon-bridge-sim -- a
 * fake Falco HTTP-output/Tetragon-bridge endpoint) or a real Falco/
 * Tetragon deployment outside it. Same GET /api/security/findings every
 * other Security page already polls -- ebpf_signal.alerts is already a
 * subset of that payload (services/ebpf_signal.py's
 * `get_node_ebpf_alerts`, called by nodes/security.py's
 * `_check_ebpf_signal`), so no new endpoint or RBAC rule was needed to
 * light this page up -- `ebpf_signal` was already one of
 * security_rbac.py's four redacted sub-signal keys from day one.
 *
 * Phase Sec-6 widened the ansible rollout (infra/ansible-sandbox/
 * simulate-ebpf.yml) from a compute-only pilot to the full `monitoring`
 * group (controllers+computes+storages -- there's no separate "network"
 * host role in this sandbox); a host with no sensor at all still reads
 * back "Clean" here rather than "Unknown", same reasoning as before, just
 * a smaller set of hosts it can now apply to.
 *
 * Node-scoped (SecurityScopeTag): a sensor sees that host's own kernel --
 * QEMU/libvirtd processes, host-level syscalls -- never anything
 * happening inside a guest VM's own kernel. That's the KVM isolation
 * boundary, not a Cortex limitation; the page copy below says so
 * explicitly rather than letting "kernel signals" read as covering guest
 * workloads.
 */
export default function KernelSignalsPage() {
  // Reads ?host=<hostname> for the deep link from a node's own "Security
  // posture" section (app/nodes/[instance]/page.tsx) -- Next's app
  // router requires useSearchParams behind a Suspense boundary (same
  // pattern as /logs).
  return (
    <Suspense fallback={null}>
      <KernelSignalsPageInner />
    </Suspense>
  );
}

function KernelSignalsPageInner() {
  const hostFilter = useSearchParams().get("host");
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const allFindings = useMemo(() => data?.findings ?? [], [data]);
  const findings = useMemo(() => filterFindingsByHost(allFindings, hostFilter), [allFindings, hostFilter]);
  const [priority, setPriority] = useState<PriorityFilter>("all");

  const allRows = useMemo(() => flattenEbpfFindings(findings), [findings]);
  const hostStatuses = useMemo(() => buildEbpfHostStatuses(findings), [findings]);

  const counts = useMemo(() => {
    const c: Record<KnownEbpfPriority, number> = { critical: 0, warning: 0, notice: 0, info: 0 };
    for (const row of allRows) {
      const key = row.priority.toLowerCase();
      if (key in c) c[key as KnownEbpfPriority] += 1;
    }
    return c;
  }, [allRows]);

  const rows = priority === "all" ? allRows : allRows.filter((r) => r.priority.toLowerCase() === priority);

  const degradedHosts = hostStatuses.filter((h) => h.tone.label === "Unknown");
  const restrictedHosts = hostStatuses.filter((h) => h.tone.label === "Admin only");
  const cleanHosts = hostStatuses.filter((h) => h.alertCount === 0 && h.tone.label === "Clean");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">Kernel-level signals (eBPF)</h1>
            <SecurityScopeTag scope="node" />
          </div>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            Live process/syscall-level alerts from a Falco- or Tetragon-style sensor, most-severe-first. Deployed to
            every controller/compute/storage host now (Phase Sec-6 widened this from Sec-4&apos;s compute-only pilot)
            -- a sensor sees that host&apos;s own kernel (QEMU/libvirtd, host-level syscalls), never inside a guest
            VM&apos;s own kernel, which no sensor placement in Cortex today can see.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          <SecurityHealthBadge />
          <SecurityRescanButton />
        </div>
      </div>

      {hostFilter && <SecurityHostFilterBanner host={hostFilter} clearHref="/security/kernel-signals" />}

      {error && (
        <Card>
          <div className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--crit)" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
            <span>Kernel-signal findings are currently unavailable.</span>
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
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {EBPF_PRIORITY_ORDER.map((p) => (
              <PriorityStat key={p} priority={p} count={counts[p]} active={priority === p} onClick={() => setPriority(priority === p ? "all" : p)} />
            ))}
          </div>

          {(degradedHosts.length > 0 || restrictedHosts.length > 0) && (
            <Card>
              <div className="flex flex-wrap items-start gap-3 text-sm">
                <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
                <div className="min-w-0 flex-1">
                  {degradedHosts.length > 0 && (
                    <p className="text-text-dim">
                      <span className="font-medium">{degradedHosts.length}</span> host{degradedHosts.length === 1 ? "" : "s"} could
                      not be checked this pass -- the kernel-signal sensor bridge didn&apos;t respond, so runtime
                      behavior there is unknown rather than confirmed clean:{" "}
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

          <EbpfAlertsTable rows={rows} />

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
