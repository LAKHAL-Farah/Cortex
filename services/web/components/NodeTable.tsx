"use client";
import Link from "next/link";
import useSWR, { mutate } from "swr";
import type { DashboardNode } from "@/lib/types";
import { Server, ShieldCheck } from "lucide-react";
import { ProgressBar } from "./ui/ProgressBar";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

type NodeTableProps = {
  nodes?: DashboardNode[];
  isLoading?: boolean;
};

export default function NodeTable({ nodes: incomingNodes, isLoading: incomingLoading }: NodeTableProps = {}) {
  const { data: fetchedNodes, isLoading: fetching } = useSWR<DashboardNode[]>("/api/nodes", fetcher, {
    refreshInterval: 5000,
  });
  const nodes = incomingNodes ?? fetchedNodes;
  const isLoading = incomingLoading ?? fetching;

  if (isLoading) return <p className="p-6 text-text-faint">Loading…</p>;
  if (!nodes?.length) return <p className="p-6 text-sm text-text-faint">No nodes registered yet.</p>;

  return (
    <div className="space-y-4">
      <div className="rounded-[20px] border border-[#ECECEC] bg-white p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Node inventory</div>
            <div className="mt-3 text-2xl font-semibold text-color-text">Recently active nodes</div>
          </div>
          <div className="inline-flex items-center gap-2 rounded-full bg-[#F8FAFC] px-4 py-3 text-sm text-text-faint">
            <ShieldCheck className="h-4 w-4 text-green-600" />
            {nodes.filter((n) => n.is_active).length} online
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div className="hidden grid-cols-[3fr_1fr_1fr_2fr_1fr] gap-4 rounded-t-[20px] bg-[#F8FAFC] p-4 text-xs uppercase tracking-[0.24em] text-text-faint sm:grid">
          <div>Node</div>
          <div>Role</div>
          <div>Status</div>
          <div>CPU</div>
          <div className="text-right">Updated</div>
        </div>

        {nodes.map((n) => {
          const nodePathValue = (n.instance && n.instance.trim()) || n.id || n.hostname || n.ip_address || "";
          const encodedNodePath = nodePathValue ? encodeURIComponent(nodePathValue) : "";
          const href = encodedNodePath ? `/nodes/${encodedNodePath}` : "/nodes";
          return (
            <Link
              key={n.id}
              href={href}
              className="group grid gap-4 rounded-[20px] border border-[#ECECEC] bg-white p-5 shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition hover:-translate-y-0.5 hover:shadow-[0_8px_24px_rgba(0,0,0,0.08)] sm:grid-cols-[3fr_1fr_1fr_2fr_1fr]"
            >
            <div className="flex items-start gap-4">
              <div className="grid h-12 w-12 place-items-center rounded-[18px] bg-orange-50 text-orange-600">
                <Server className="h-5 w-5" />
              </div>
              <div>
                <div className="font-semibold text-color-text">{n.hostname}</div>
                <div className="mt-1 text-sm text-text-faint">{n.ip_address} · {n.role}</div>
              </div>
            </div>

            <div className="hidden sm:block">
              <div className="text-sm font-semibold text-color-text">{n.role}</div>
            </div>

            <div>
              <span className={`inline-flex rounded-full px-3 py-1 text-sm font-semibold ${n.is_active ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                {n.is_active ? 'Active' : 'Inactive'}
              </span>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2 text-sm text-text-faint">
                <span>CPU usage</span>
                <span>{n.metrics?.cpu_percent ?? 0}%</span>
              </div>
              <ProgressBar value={n.metrics?.cpu_percent ?? 0} />
            </div>

            <div className="text-right text-sm text-text-faint">
              {n.updated_at ? new Date(n.updated_at).toLocaleDateString() : n.created_at ? new Date(n.created_at).toLocaleDateString() : "—"}
            </div>
          </Link>
        );
      })}
      </div>
    </div>
  );
}
