"use client";
import useSWR from "swr";
import NodeCard from "@/components/NodeCard";
import type { DashboardNode } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function DashboardPage() {
  const { data: nodes, isLoading } = useSWR<DashboardNode[]>("/api/dashboard", fetcher, {
    refreshInterval: 5000,
  });

  if (isLoading) return <p className="p-6">Loading…</p>;
  if (!nodes?.length) return <p className="p-6 text-gray-500">No nodes registered. Add one on the Nodes page.</p>;

  const up = nodes.filter((n) => n.metrics?.status === "up").length;

  return (
    <main className="max-w-6xl mx-auto p-6 space-y-6">
      <div className="flex gap-6 text-sm text-gray-600">
        <div>Total: {nodes.length}</div>
        <div>Up: {up}</div>
        <div>Down/pending: {nodes.length - up}</div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {nodes.map((n) => <NodeCard key={n.id} n={n} />)}
      </div>
    </main>
  );
}