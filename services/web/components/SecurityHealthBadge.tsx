"use client";

import useSWR from "swr";
import { RefreshCw } from "lucide-react";
import type { SecurityHealth, TopologySyncStatus } from "@/lib/types";
import { SYNC_STATUS_COLOR, SYNC_STATUS_LABEL, SYNC_STATUS_SOFT, formatRelative } from "@/lib/topology";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** "Scanned Xm ago" badge for the Security pages, same shape and same
 * ok/degraded/unknown/failed vocabulary TopologyHealthBadge already gives
 * the topology page -- reusing lib/topology.ts's SYNC_STATUS_* maps
 * rather than a second copy, since the vocabulary means the same thing
 * here (see main.py's `_security_scan_status`).
 *
 * Backed by GET /api/v1/security/health (services/security_scan_cache.py
 * + models.SecurityScanRun), i.e. actual scan-pass run history, not a
 * live recomputation -- the whole point of Phase Sec-3's caching pass is
 * that this can poll far more cheaply than a live scan would.
 */
export default function SecurityHealthBadge() {
  const { data, error, isLoading } = useSWR<SecurityHealth>("/api/security/health", fetcher, {
    refreshInterval: 10000,
  });

  if (isLoading) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium text-text-faint">
        <RefreshCw className="h-3 w-3 animate-spin" strokeWidth={2} />
        Checking scan…
      </span>
    );
  }

  if (error || !data) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
        style={{ color: "var(--crit)", background: "var(--crit-soft)" }}
        title="Could not reach GET /api/v1/security/health"
      >
        <span className="status-dot" style={{ background: "var(--crit)" }} />
        Health unavailable
      </span>
    );
  }

  const status = data.status as TopologySyncStatus;
  const label = SYNC_STATUS_LABEL[status];
  const color = SYNC_STATUS_COLOR[status];
  const soft = SYNC_STATUS_SOFT[status];
  const finishedAt = data.last_run?.finished_at ?? null;

  const tooltip = data.last_run
    ? `security scan: ${data.last_run.status} (finished ${formatRelative(data.last_run.finished_at)})`
    : "no scan pass has run yet";

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ color, background: soft }}
      title={tooltip}
    >
      <span
        className={status === "ok" ? "status-dot glow-pulse" : "status-dot"}
        style={{ background: color, ["--pulse-color" as string]: color }}
      />
      {finishedAt ? `Scanned ${formatRelative(finishedAt)}` : label}
    </span>
  );
}
