"use client";

import { useMemo, useState, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, ScrollText, AlertTriangle, RefreshCw, ShieldQuestion, ShieldCheck } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";
import SecurityScopeTag from "@/components/SecurityScopeTag";
import SecurityHostFilterBanner from "@/components/SecurityHostFilterBanner";
import CveFindingsTable from "@/components/CveFindingsTable";
import { CVE_SEVERITY_ORDER, buildCveHostStatuses, cveSeverityTone, filterFindingsByHost, flattenCveFindings, type KnownCveSeverity } from "@/lib/securityStatus";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

type SeverityFilter = "all" | KnownCveSeverity;

/** Clickable severity count chip -- same shape/behavior as AlertsView's
 * local StatCard (toggle the filter on click, click again to clear), just
 * defined here since that one isn't exported and this page's filter set
 * (the four cve_feed severities, not anomaly severities) is different. */
function SeverityStat({
  severity,
  count,
  active,
  onClick,
}: {
  severity: KnownCveSeverity;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  const tone = cveSeverityTone(severity);
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
        <ScrollText className="h-4.5 w-4.5" style={{ color: tone.color }} strokeWidth={1.75} />
      </div>
      <div className="min-w-0">
        <div className="stat-figure text-xl text-color-text">{count}</div>
        <div className="truncate text-xs text-text-faint">{tone.label}</div>
      </div>
    </button>
  );
}

/** Phase Sec-3: real CVE-match data, unblocked by the package-inventory
 * collector (services/cve_feed.py's `get_package_inventory`, now pointed
 * at a real host in every deployed environment -- in the sandbox,
 * openstack-sim's own `/packages` route). Same GET /api/security/findings
 * every other Security page already polls -- cve_signal.matches is
 * already a subset of that payload, so no new endpoint was needed, same
 * as /security/security-groups reusing it for sec_group_signal.
 *
 * Node-scoped (SecurityScopeTag): this reads a host's own installed OS
 * packages, never anything inside a guest VM.
 */
export default function VulnerabilitiesPage() {
  // Reads ?host=<hostname> for the deep link from a node's own "Security
  // posture" section (app/nodes/[instance]/page.tsx) -- Next's app
  // router requires useSearchParams behind a Suspense boundary (same
  // pattern as /logs).
  return (
    <Suspense fallback={null}>
      <VulnerabilitiesPageInner />
    </Suspense>
  );
}

function VulnerabilitiesPageInner() {
  const hostFilter = useSearchParams().get("host");
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const allFindings = useMemo(() => data?.findings ?? [], [data]);
  const findings = useMemo(() => filterFindingsByHost(allFindings, hostFilter), [allFindings, hostFilter]);
  const [severity, setSeverity] = useState<SeverityFilter>("all");

  const allRows = useMemo(() => flattenCveFindings(findings), [findings]);
  const hostStatuses = useMemo(() => buildCveHostStatuses(findings), [findings]);

  const counts = useMemo(() => {
    const c: Record<KnownCveSeverity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const row of allRows) {
      if (row.severity in c) c[row.severity as KnownCveSeverity] += 1;
    }
    return c;
  }, [allRows]);

  const rows = severity === "all" ? allRows : allRows.filter((r) => r.severity === severity);

  const degradedHosts = hostStatuses.filter((h) => h.tone.label === "Unknown");
  const restrictedHosts = hostStatuses.filter((h) => h.tone.label === "Admin only");
  const cleanHosts = hostStatuses.filter((h) => h.matchCount === 0 && h.tone.label === "Clean");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">Vulnerabilities (CVE)</h1>
            <SecurityScopeTag scope="node" />
          </div>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            Installed package versions on every monitored controller/compute/storage/monitoring host, matched
            against a small, hand-picked table of known CVEs (openssh-server, openssl, sudo, runc, libvirt).
            This reads the host&apos;s own OS packages only -- a guest VM&apos;s own installed packages aren&apos;t
            visible here or anywhere else in Cortex today.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 pt-1">
          <SecurityHealthBadge />
          <SecurityRescanButton />
        </div>
      </div>

      {hostFilter && <SecurityHostFilterBanner host={hostFilter} clearHref="/security/vulnerabilities" />}

      {error && (
        <Card>
          <div className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--crit)" }}>
            <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
            <span>CVE findings are currently unavailable.</span>
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
            {CVE_SEVERITY_ORDER.map((sev) => (
              <SeverityStat
                key={sev}
                severity={sev}
                count={counts[sev]}
                active={severity === sev}
                onClick={() => setSeverity(severity === sev ? "all" : sev)}
              />
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
                      not be checked this pass -- the package-inventory collector didn&apos;t respond, so known-vulnerable packages
                      there are unknown rather than confirmed absent:{" "}
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

          <CveFindingsTable rows={rows} />

          {cleanHosts.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-text-faint">
              <ShieldCheck className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} strokeWidth={2} />
              <span>
                Also checked and clean: {cleanHosts.map((h) => h.hostname).join(", ")}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
