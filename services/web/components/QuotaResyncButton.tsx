"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { AnimatePresence, motion } from "framer-motion";
import { Wallet, Loader2, CheckCircle2, XCircle } from "lucide-react";
import type { QuotaResyncSummary } from "@/lib/types";

type Phase = "idle" | "running" | "ok" | "failed";

/** "Check now" -- manual, on-demand trigger for the same quota/budget pass
 * main.py's periodic loop already runs every
 * QUOTA_BUDGET_CHECK_INTERVAL_SECONDS (see routers/quotas.py's
 * POST /resync). Same idle -> running -> outcome shape as
 * TopologyResyncButton.tsx, simplified to ok/failed since a quota check
 * pass doesn't have a "degraded" state -- it either reaches OpenStack and
 * produces a summary, or it doesn't.
 */
export default function QuotaResyncButton() {
  const { mutate } = useSWRConfig();
  const [phase, setPhase] = useState<Phase>("idle");
  const [lastSummary, setLastSummary] = useState<QuotaResyncSummary["summary"] | null>(null);

  const isBusy = phase === "running";

  const run = async () => {
    if (isBusy) return;
    setPhase("running");
    try {
      const res = await fetch("/api/quotas/resync", { method: "POST" });
      const data = (await res.json().catch(() => null)) as (QuotaResyncSummary & { detail?: string }) | null;
      if (!res.ok || !data) {
        setPhase("failed");
      } else {
        setPhase("ok");
        setLastSummary(data.summary ?? null);
      }
    } catch {
      setPhase("failed");
    } finally {
      mutate("/api/quotas/alerts");
      setTimeout(() => setPhase("idle"), 2600);
    }
  };

  return (
    <div className="flex items-center gap-2">
      {phase === "ok" && lastSummary && (
        <span className="text-xs text-text-faint">
          {lastSummary.projects_checked} project{lastSummary.projects_checked === 1 ? "" : "s"} checked
        </span>
      )}
      <button
        onClick={run}
        disabled={isBusy}
        title="Run an immediate quota/budget check against OpenStack"
        className="relative inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-control)] px-3 text-sm font-medium transition-colors disabled:cursor-wait"
        style={{
          border: "1px solid var(--border)",
          color: phase === "idle" || phase === "running" ? "var(--text)" : phase === "ok" ? "var(--ok)" : "var(--crit)",
        }}
      >
        <AnimatePresence mode="wait" initial={false}>
          {phase === "idle" && (
            <motion.span key="idle" className="inline-flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <Wallet className="h-3.5 w-3.5" strokeWidth={2} />
              Check now
            </motion.span>
          )}
          {phase === "running" && (
            <motion.span key="running" className="inline-flex items-center gap-1.5" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
              Checking…
            </motion.span>
          )}
          {phase === "ok" && (
            <motion.span key="ok" className="inline-flex items-center gap-1.5" initial={{ opacity: 0, y: -2 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2} />
              Checked
            </motion.span>
          )}
          {phase === "failed" && (
            <motion.span key="failed" className="inline-flex items-center gap-1.5" initial={{ opacity: 0, y: -2 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <XCircle className="h-3.5 w-3.5" strokeWidth={2} />
              Check failed
            </motion.span>
          )}
        </AnimatePresence>
      </button>
    </div>
  );
}
