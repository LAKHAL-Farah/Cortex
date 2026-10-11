"use client";

import Link from "next/link";
import Widget, { Skeleton } from "./Widget";
import { useFleet, useLogs } from "@/lib/useDashboardData";
import type { NodeRole } from "@/lib/types";

const ROLE_COLOR: Record<NodeRole, string> = {
  controller: "var(--role-controller)",
  compute: "var(--role-compute)",
  storage: "var(--role-storage)",
  monitoring: "var(--role-monitoring)",
};
const load = (v: number) => (v >= 90 ? "var(--crit)" : v >= 75 ? "var(--warn)" : "var(--ok)");

function Bar({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-2 text-[10px] text-text-faint">
      <span className="w-7">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full" style={{ background: "var(--canvas)" }}>
        <div className="h-full rounded-full" style={{ width: `${Math.min(value, 100)}%`, background: load(value) }} />
      </div>
      <span className="stat-figure w-9 text-right text-text-dim">{Math.round(value)}%</span>
    </div>
  );
}

export default function NodesGrid() {
  const { data: nodes, isLoading } = useFleet();
  const { parsed } = useLogs();
  const issues: Record<string, number> = {};
  for (const l of parsed) if (l.host && (l.level === "ERROR" || l.level === "WARNING")) issues[l.host] = (issues[l.host] ?? 0) + 1;
  const up = (nodes ?? []).filter((n) => n.metrics?.status === "up").length;
  const count = nodes?.length ?? 0;
  const cols = count <= 5 ? Math.max(count, 1) : count === 6 ? 3 : 4;

  return (
    <Widget eyebrow="Fleet" title="Nodes at a glance" href="/nodes" hrefLabel="All nodes" right={<span className="text-xs text-text-faint">{up}/{count} online</span>}>
      {isLoading ? (
        <Skeleton h={110} />
      ) : (
        // Column count follows the fleet size so a 5-node cluster fills one row
        // instead of leaving a lonely card on a second line.
        <div
          className="grid gap-3 sm:grid-cols-2 xl:[grid-template-columns:repeat(var(--cols),minmax(0,1fr))]"
          style={{ ["--cols" as string]: cols }}
        >
          {(nodes ?? []).map((n) => {
            const m = n.metrics;
            const isUp = m?.status === "up";
            const path = ((n.instance && n.instance.trim()) || n.id || n.hostname).trim();
            const issueCount = issues[n.hostname] ?? 0;
            return (
              <Link key={n.id} href={`/nodes/${encodeURIComponent(path)}`} className="panel-interactive block rounded-[var(--radius-panel)] p-3" style={{ border: "1px solid var(--border-soft)", background: "var(--canvas)" }}>
                <div className="flex items-center gap-2">
                  <span className="status-dot flex-shrink-0" style={{ background: isUp ? "var(--ok)" : "var(--crit)" }} />
                  <span className="truncate text-[13px] font-medium text-color-text">{n.hostname}</span>
                  {issueCount > 0 && <span className="ml-auto flex-shrink-0 rounded-full px-1.5 py-px text-[10px] font-semibold" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>{issueCount}</span>}
                </div>
                <div className="mb-2 mt-0.5 text-[11px]" style={{ color: ROLE_COLOR[n.role] ?? "var(--text-faint)" }}>{n.role}</div>
                {isUp && m ? (
                  <div className="space-y-1.5">
                    <Bar label="CPU" value={m.cpu_percent} />
                    <Bar label="MEM" value={m.memory_percent} />
                    <Bar label="DSK" value={m.disk_percent} />
                  </div>
                ) : (
                  <div className="py-3 text-xs" style={{ color: "var(--crit)" }}>Not responding</div>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </Widget>
  );
}
