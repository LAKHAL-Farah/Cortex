"use client";

import useSWR from "swr";
import Link from "next/link";
import Widget, { MiniStat, Pill, Skeleton, Unavailable } from "./Widget";
import { apiFetcher } from "@/lib/useDashboardData";
import type { SecurityFinding, SecurityStatus } from "@/lib/types";
import { overallSecurityTone } from "@/lib/securityStatus";
import { formatRelative } from "@/lib/anomalies";

export default function SecurityWidget() {
  const opts = { refreshInterval: 60_000, shouldRetryOnError: false } as const;
  const status = useSWR<SecurityStatus>("/api/security/status", apiFetcher, opts);
  // GET /api/v1/security/findings returns `{ findings: [...] }`, not a bare
  // array (the /security page reads `data.findings` too). Treating it as an
  // array made every count below 0. Accept both shapes defensively.
  const findings = useSWR<{ findings: SecurityFinding[] } | SecurityFinding[]>("/api/security/findings", apiFetcher, opts);

  const raw = findings.data;
  const list: SecurityFinding[] = Array.isArray(raw) ? raw : Array.isArray(raw?.findings) ? raw.findings : [];
  const tones = list.map((f) => ({ f, tone: overallSecurityTone(f.raw_data) }));
  const flagged = tones.filter((t) => t.tone.label === "Flagged");
  const degraded = tones.filter((t) => t.tone.label === "Degraded");
  const s = status.data;
  const overall = flagged.length ? { c: "var(--crit)", s: "var(--crit-soft)", l: "Signals found" } : degraded.length || s?.degraded ? { c: "var(--warn)", s: "var(--warn-soft)", l: "Partial data" } : { c: "var(--ok)", s: "var(--ok-soft)", l: "Clean" };

  return (
    <Widget eyebrow="Security" title="Posture" href="/security" right={s || list.length ? <Pill color={overall.c} soft={overall.s}>{overall.l}</Pill> : null}>
      {status.isLoading && findings.isLoading ? (
        <Skeleton h={130} />
      ) : status.error && findings.error ? (
        <Unavailable />
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            <MiniStat label="Hosts scanned" value={list.length || (s?.node_count ?? 0)} />
            <MiniStat label="Flagged" value={flagged.length} color={flagged.length ? "var(--crit)" : "var(--ok)"} />
            <MiniStat label="Degraded" value={degraded.length} color={degraded.length ? "var(--warn)" : undefined} />
          </div>
          {flagged.length > 0 && (
            <div className="mt-3 space-y-1">
              {flagged.slice(0, 3).map(({ f }) => (
                <Link key={f.hostname} href={`/nodes`} className="flex items-center justify-between rounded-[var(--radius-control)] px-2.5 py-1.5 text-xs hover:bg-[var(--canvas)]">
                  <span className="truncate font-medium text-color-text">{f.hostname}</span>
                  <span className="ml-2 flex-shrink-0 text-text-faint">{f.role}</span>
                </Link>
              ))}
            </div>
          )}
          <div className="mt-3 text-[11px] text-text-faint">
            {s?.last_scan_at ? `Last scan ${formatRelative(s.last_scan_at)}` : "No scan recorded yet"}
            {s?.sandbox_mode ? " · sandbox mode" : ""}
          </div>
        </>
      )}
    </Widget>
  );
}
