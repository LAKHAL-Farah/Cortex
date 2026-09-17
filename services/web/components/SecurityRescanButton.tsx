"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { AnimatePresence, motion } from "framer-motion";
import { ShieldCheck, Loader2, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import type { SecurityScanRun } from "@/lib/types";

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
  ok: "Rescanned",
  degraded: "Partial rescan",
  failed: "Rescan failed",
};

/** "Rescan" -- a manual, on-demand trigger for the same fleet-wide
 * Security Agent scan pass main.py's periodic loop already runs every
 * SECURITY_SCAN_INTERVAL_SECONDS (see routers/security.py's new
 * POST /resync). Direct sibling of TopologyResyncButton's "Reconverge"
 * control -- same idle/running/outcome animation, same "revalidate every
 * SWR key this could have changed" cleanup -- just for the Security
 * Agent's cache (security_finding_cache) instead of the topology graph.
 *
 * Revalidates every /api/security/* key on completion: /findings (the
 * overview + security-groups list pages), /health (this button's own
 * sibling badge), /status (the sandbox-mode/last-scan strip), and every
 * /groups/{hostname} key currently mounted (the per-host detail page) --
 * a rescan can change any of them, and a degraded/failed pass can still
 * have partially refreshed some hosts' cache rows (security_scan_cache.
 * run_security_scan keeps going after one node's sub-checks fail), so
 * all of them should reflect whatever actually happened rather than
 * waiting out their own poll interval.
 */
export default function SecurityRescanButton() {
  const { mutate } = useSWRConfig();
  const [phase, setPhase] = useState<Phase>("idle");

  const isBusy = phase === "running";

  const run = async () => {
    if (isBusy) return;
    setPhase("running");
    try {
      const res = await fetch("/api/security/resync", { method: "POST" });
      const data = (await res.json().catch(() => null)) as (SecurityScanRun & { detail?: string }) | null;
      if (!res.ok || !data) {
        setPhase("failed");
      } else {
        setPhase(data.status === "ok" ? "ok" : data.status === "degraded" ? "degraded" : "failed");
      }
    } catch {
      setPhase("failed");
    } finally {
      mutate("/api/security/health");
      mutate("/api/security/status");
      mutate("/api/security/findings");
      mutate((key) => typeof key === "string" && key.startsWith("/api/security/groups/"));
      setTimeout(() => setPhase("idle"), 2600);
    }
  };

  return (
    <button
      onClick={run}
      disabled={isBusy}
      title="Trigger an immediate security scan pass across every known node"
      className="relative inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-control)] px-3 text-sm font-medium transition-colors disabled:cursor-wait"
      style={{
        border: "1px solid var(--border)",
        color: phase === "idle" || phase === "running" ? "var(--text)" : PHASE_COLOR[phase],
      }}
    >
      <AnimatePresence mode="wait" initial={false}>
        {phase === "idle" && (
          <motion.span key="idle" className="inline-flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <ShieldCheck className="h-3.5 w-3.5" strokeWidth={2} />
            Rescan
          </motion.span>
        )}
        {phase === "running" && (
          <motion.span key="running" className="inline-flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            Rescanning…
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
