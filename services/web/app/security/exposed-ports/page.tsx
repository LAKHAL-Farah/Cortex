"use client";

import { useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowLeft, AlertTriangle, RefreshCw, ShieldQuestion, ShieldCheck } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import SecurityHealthBadge from "@/components/SecurityHealthBadge";
import SecurityRescanButton from "@/components/SecurityRescanButton";
import SecurityScopeTag from "@/components/SecurityScopeTag";
import ExposedPortsTable from "@/components/ExposedPortsTable";
import InstanceExposureLookup from "@/components/InstanceExposureLookup";
import { buildExposedPortHostStatuses, flattenExposedPortFindings } from "@/lib/securityStatus";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** Phase Sec-5a/5b: real exposed-port data, unblocked by the listening-
 * port collector (services/exposed_ports.py, extending Sec-3's own
 * package-inventory collector with `/listening-ports`). Same GET
 * /api/security/findings every other Security page already polls --
 * exposed_port_signal is already a subset of that payload, no new
 * fleet-wide endpoint needed, same as /security/vulnerabilities reusing
 * it for cve_signal.
 *
 * Node-scoped for the main table (SecurityScopeTag): a security-group
 * rule that world-opens a port range, confirmed backed by a real
 * listening socket on that host. Sec-5b below is genuinely
 * Instance-scoped and, unlike Sec-5a, isn't part of any periodic scan --
 * see InstanceExposureLookup's own docstring.
 */
export default function ExposedPortsPage() {
  const { data, error, isLoading, mutate } = useSWR<{ findings: SecurityFinding[] }>(
    "/api/security/findings",
    fetcher,
    { refreshInterval: 15000, revalidateOnFocus: true },
  );

  const findings = useMemo(() => data?.findings ?? [], [data]);
  const rows = useMemo(() => flattenExposedPortFindings(findings), [findings]);
  const hostStatuses = useMemo(() => buildExposedPortHostStatuses(findings), [findings]);

  const degradedHosts = hostStatuses.filter((h) => h.tone.label === "Unknown");
  const restrictedHosts = hostStatuses.filter((h) => h.tone.label === "Admin only");
  const cleanHosts = hostStatuses.filter((h) => h.mismatchCount === 0 && h.tone.label === "Clean");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href="/security" className="mb-2 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
            <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
            Back to Security
          </Link>
          <div className="flex items-center gap-2">
            <h1 className="font-display text-[22px] font-semibold text-color-text">Exposed ports</h1>
            <SecurityScopeTag scope="node" />
          </div>
          <p className="mt-1 max-w-2xl text-sm text-text-faint">
            Every controller/compute/storage/monitoring host&apos;s own real listening sockets, cross-checked
            against its security groups&apos; world-open rules. Only the intersection is flagged: a rule alone
            (nothing listening behind it) or a listening port alone (no rule reaching it from outside) isn&apos;t a
            confirmed exposure by itself.
          </p>
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
            <span>Exposed-port findings are currently unavailable.</span>
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
          {(degradedHosts.length > 0 || restrictedHosts.length > 0) && (
            <Card>
              <div className="flex flex-wrap items-start gap-3 text-sm">
                <ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
                <div className="min-w-0 flex-1">
                  {degradedHosts.length > 0 && (
                    <p className="text-text-dim">
                      <span className="font-medium">{degradedHosts.length}</span> host{degradedHosts.length === 1 ? "" : "s"} could
                      not be checked this pass -- the listening-port collector didn&apos;t respond, so confirmed exposure
                      there is unknown rather than confirmed absent:{" "}
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

          <ExposedPortsTable rows={rows} />

          {cleanHosts.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-text-faint">
              <ShieldCheck className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} strokeWidth={2} />
              <span>Also checked and clean: {cleanHosts.map((h) => h.hostname).join(", ")}</span>
            </div>
          )}
        </>
      )}

      <InstanceExposureLookup />
    </div>
  );
}
