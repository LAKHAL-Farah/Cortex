import type { DashboardNode, NodeRole } from "@/lib/types";
import Link from "next/link";
import { Cpu, MemoryStick, HardDrive, Clock3, Activity, TriangleAlert } from "lucide-react";
import { Card } from "./ui/Card";
import Sparkline from "./Sparkline";

const HEALTH_COLOR: Record<string, string> = {
  healthy: "var(--ok)",
  warning: "var(--warn)",
  critical: "var(--crit)",
};
const HEALTH_SOFT: Record<string, string> = {
  healthy: "var(--ok-soft)",
  warning: "var(--warn-soft)",
  critical: "var(--crit-soft)",
};

const ROLE_COLOR: Record<NodeRole, string> = {
  controller: "var(--role-controller)",
  compute: "var(--role-compute)",
  storage: "var(--role-storage)",
  monitoring: "var(--role-monitoring)",
};
const ROLE_SOFT: Record<NodeRole, string> = {
  controller: "var(--role-controller-soft)",
  compute: "var(--role-compute-soft)",
  storage: "var(--role-storage-soft)",
  monitoring: "var(--role-monitoring-soft)",
};

function Stat({ icon: Icon, label, value, color }: { icon: any; label: string; value: React.ReactNode; color: string }) {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium" style={{ background: "var(--canvas)", color: "var(--text-dim)" }}>
      <Icon className="h-3 w-3" style={{ color }} strokeWidth={2.25} />
      <span className="stat-figure">{value}</span>
      <span className="text-text-faint">{label}</span>
    </div>
  );
}

export default function NodeOverviewCard({ n, issueCount = 0 }: { n: DashboardNode; issueCount?: number }) {
  const nodePathValue = ((n.instance && n.instance.trim()) || n.id || n.hostname || n.ip_address || "").trim();
  const encodedNodePath = nodePathValue ? encodeURIComponent(nodePathValue) : "";
  const href = encodedNodePath ? `/nodes/${encodedNodePath}` : "/nodes";
  const roleColor = ROLE_COLOR[n.role] ?? "var(--accent)";
  const roleSoft = ROLE_SOFT[n.role] ?? "var(--accent-soft)";
  const m = n.metrics;

  return (
    <Link href={href} className="block">
      <Card interactive padding="p-0" className="relative overflow-hidden">
        <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: roleColor }} />

        <div className="p-4 pl-5">
          <div className="flex items-center justify-between gap-3">
            <span
              className="inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide"
              style={{ color: roleColor, background: roleSoft }}
            >
              {n.role}
            </span>

            <div className="flex items-center gap-1.5">
              {issueCount > 0 && (
                <span
                  className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold"
                  style={{ color: "var(--crit)", background: "var(--crit-soft)" }}
                  title={`${issueCount} warning/error log${issueCount === 1 ? "" : "s"} in the last hour`}
                >
                  <TriangleAlert className="h-3 w-3" strokeWidth={2.25} />
                  {issueCount}
                </span>
              )}
              {m ? (
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium"
                  style={{ color: HEALTH_COLOR[m.health], background: HEALTH_SOFT[m.health] }}
                >
                  <span className="status-dot" style={{ background: HEALTH_COLOR[m.health] }} />
                  {m.status === "up" ? "Online" : "Offline"}
                </span>
              ) : (
                <span className="text-xs text-text-faint">No data</span>
              )}
            </div>
          </div>

          <div className="mt-3 flex items-end justify-between gap-3">
            <div className="min-w-0">
              <div className="font-display truncate text-[17px] font-semibold text-color-text">{n.hostname}</div>
              <div className="mt-0.5 truncate text-sm text-text-faint">{n.ip_address}:{n.exporter_port}</div>
            </div>
            {m && (
              <div className="h-[32px] w-[92px] flex-shrink-0">
                <Sparkline id={`dash-cpu-${n.id}`} value={m.cpu_percent} color={roleColor} />
              </div>
            )}
          </div>

          {m ? (
            <>
              <div className="mt-4 flex flex-wrap gap-2">
                <Stat icon={Cpu} label="CPU" value={`${m.cpu_percent}%`} color="var(--chart-1)" />
                <Stat icon={MemoryStick} label="Mem" value={`${m.memory_percent}%`} color="var(--chart-4)" />
                <Stat icon={HardDrive} label="Disk" value={`${m.disk_percent}%`} color="var(--chart-5)" />
              </div>

              <div className="mt-4 flex items-center justify-between border-t pt-3 text-xs text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
                <span className="inline-flex items-center gap-1.5">
                  <Activity className="h-3.5 w-3.5" strokeWidth={2} />
                  {m.procs_running} running · {m.procs_blocked} blocked
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <Clock3 className="h-3.5 w-3.5" strokeWidth={2} />
                  {m.uptime}
                </span>
              </div>
            </>
          ) : (
            <div className="mt-4 text-sm text-text-faint">Registered, waiting for first Prometheus scrape…</div>
          )}
        </div>
      </Card>
    </Link>
  );
}
