"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { AnimatePresence, motion } from "framer-motion";
import { GitBranch, Loader2, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import type { TopologySyncRun } from "@/lib/types";

type Phase = "idle" | "running" | "ok" | "degraded" | "failed";

const PHASE_ICON: Record<Exclude<Phase, "idle" | "running">, typeof CheckCircle2> = {
  ok: CheckCircle2,
  degraded: AlertTriangle,
  failed: XCircle,
};

const PHASE_COLOR: Record<Exclude<Phase, "idle" | "running">, string> = {
  ok: "var(--ok)",
  degraded: "var(--warn)",
  failed: "var(--crit)",
};

const PHASE_LABEL: Record<Exclude<Phase, "idle" | "running">, string> = {
  ok: "Reconverged",
  degraded: "Partial resync",
  failed: "Resync failed",
};

/** "Reconverge" -- a manual, on-demand trigger for the same OpenStack
 * topology-sync pass main.py's periodic loop already runs every
 * TOPOLOGY_SYNC_INTERVAL_SECONDS (see routers/topology.py's new
 * POST /resync). Named after what a routing/graph topology actually does
 * after a change -- re-derive a consistent picture from the latest
 * source of truth -- rather than a generic "Sync now", to fit the rest of
 * this page's networking vocabulary (RUNS_ON/SERVES/CONNECTS, sync
 * health, ...).
 *
 * Sits next to TopologyHealthBadge in the top bar: that badge is the
 * passive "when did this last happen" read, this button is the active
 * "make it happen right now" control. On completion it revalidates the
 * health badge's SWR key (and, since a resync can add/remove vertices,
 * the graph itself) so both reflect the new pass immediately instead of
 * waiting for their own poll interval.
 */
export default function TopologyResyncButton() {
  const { mutate } = useSWRConfig();
  const [phase, setPhase] = useState<Phase>("idle");

  const isBusy = phase === "running";

  const run = async () => {
    if (isBusy) return;
    setPhase("running");
    try {
      const res = await fetch("/api/topology/resync", { method: "POST" });
      const data = (await res.json().catch(() => null)) as (TopologySyncRun & { detail?: string }) | null;
      if (!res.ok || !data) {
        setPhase("failed");
      } else {
        setPhase(data.status === "ok" ? "ok" : data.status === "degraded" ? "degraded" : "failed");
      }
    } catch {
      setPhase("failed");
    } finally {
      // Both keys get pulled fresh regardless of outcome -- a degraded or
      // failed pass can still have partially updated the graph (mark-and-
      // sweep runs per-listing, see topology_sync.py), so the health
      // badge and canvas should reflect whatever actually happened.
      mutate("/api/topology/health");
      mutate("/api/topology");
      // Settle back to idle after the outcome has had a moment to register.
      setTimeout(() => setPhase("idle"), 2600);
    }
  };

  return (
    <button
      onClick={run}
      disabled={isBusy}
      title="Trigger an immediate topology resync from OpenStack + Prometheus"
      className="relative inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-control)] px-3 text-sm font-medium transition-colors disabled:cursor-wait"
      style={{
        border: "1px solid var(--border)",
        color: phase === "idle" || phase === "running" ? "var(--text)" : PHASE_COLOR[phase],
      }}
    >
      <AnimatePresence mode="wait" initial={false}>
        {phase === "idle" && (
          <motion.span key="idle" className="inline-flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <GitBranch className="h-3.5 w-3.5" strokeWidth={2} />
            Reconverge
          </motion.span>
        )}
        {phase === "running" && (
          <motion.span key="running" className="inline-flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            Reconverging…
          </motion.span>
        )}
        {(phase === "ok" || phase === "degraded" || phase === "failed") &&
          (() => {
            const Icon = PHASE_ICON[phase];
            return (
              <motion.span
                key={phase}
                className="inline-flex items-center gap-1.5"
                initial={{ opacity: 0, y: -2 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
              >
                <Icon className="h-3.5 w-3.5" strokeWidth={2} />
                {PHASE_LABEL[phase]}
              </motion.span>
            );
          })()}
      </AnimatePresence>
    </button>
  );
}
