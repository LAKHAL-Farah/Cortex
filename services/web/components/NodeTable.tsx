"use client";
import Link from "next/link";
import useSWR from "swr";
import type { DashboardNode, NodeRole } from "@/lib/types";
import { Server, ShieldCheck } from "lucide-react";
import { ProgressBar } from "./ui/ProgressBar";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

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

type NodeTableProps = {
  nodes?: DashboardNode[];
  isLoading?: boolean;
};

export default function NodeTable({ nodes: incomingNodes, isLoading: incomingLoading }: NodeTableProps = {}) {
  // /api/dashboard carries live metrics; /api/nodes only returns the static
  // registration record (hostname/ip/role), which is why CPU was always 0%.
  const { data: fetchedNodes, isLoading: fetching } = useSWR<DashboardNode[]>("/api/dashboard", fetcher, {
    refreshInterval: 5000,
  });
  const nodes = incomingNodes ?? fetchedNodes;
  const isLoading = incomingLoading ?? fetching;

  if (isLoading) return <p className="p-6 text-sm text-text-faint">Loading…</p>;
  if (!nodes?.length) return <p className="p-6 text-sm text-text-faint">No nodes registered yet.</p>;

  return (
    <div className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-4 border-b p-5" style={{ borderColor: "var(--border-soft)" }}>
        <div className="text-sm font-semibold text-color-text">Recently active nodes</div>
        <div className="inline-flex items-center gap-1.5 text-sm text-text-faint">
          <ShieldCheck className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} strokeWidth={2} />
          {nodes.filter((n) => n.is_active).length} online
        </div>
      </div>

      <div className="hidden grid-cols-[3fr_1fr_1fr_2fr_1fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid" style={{ background: "var(--canvas)" }}>
        <div>Node</div>
        <div>Role</div>
        <div>Status</div>
        <div>CPU</div>
        <div className="text-right">Updated</div>
      </div>

      <div>
        {nodes.map((n) => {
          const nodePathValue = (n.instance && n.instance.trim()) || n.id || n.hostname || n.ip_address || "";
          const encodedNodePath = nodePathValue ? encodeURIComponent(nodePathValue) : "";
          const href = encodedNodePath ? `/nodes/${encodedNodePath}` : "/nodes";
          const roleColor = ROLE_COLOR[n.role] ?? "var(--accent)";
          const roleSoft = ROLE_SOFT[n.role] ?? "var(--accent-soft)";
          const cpu = n.metrics?.cpu_percent ?? 0;
          return (
            <Link
              key={n.id}
              href={href}
              className="group grid gap-4 border-b p-5 transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[3fr_1fr_1fr_2fr_1fr]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <div className="flex items-center gap-3">
                <div className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-[var(--radius-control)]" style={{ background: roleSoft }}>
                  <Server className="h-4 w-4" style={{ color: roleColor }} strokeWidth={1.75} />
                </div>
                <div>
                  <div className="font-medium text-color-text">{n.hostname}</div>
                  <div className="mt-0.5 text-sm text-text-faint">{n.ip_address} · {n.role}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <span className="text-sm text-text-dim">{n.role}</span>
              </div>

              <div className="flex items-center">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                  style={{
                    color: n.is_active ? "var(--ok)" : "var(--crit)",
                    background: n.is_active ? "var(--ok-soft)" : "var(--crit-soft)",
                  }}
                >
                  <span className="status-dot" style={{ background: n.is_active ? "var(--ok)" : "var(--crit)" }} />
                  {n.is_active ? "Active" : "Inactive"}
                </span>
              </div>

              <div className="flex flex-col justify-center gap-1.5">
                <div className="flex items-center justify-between gap-2 text-xs text-text-faint">
                  <span>CPU</span>
                  <span className="stat-figure text-color-text">{cpu}%</span>
                </div>
                <ProgressBar value={cpu} />
              </div>

              <div className="flex items-center justify-end text-sm text-text-faint">
                {n.updated_at ? new Date(n.updated_at).toLocaleDateString() : n.created_at ? new Date(n.created_at).toLocaleDateString() : "—"}
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}