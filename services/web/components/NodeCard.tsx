import Sparkline from "./Sparkline";
import type { DashboardNode } from "@/lib/types";
import Link from "next/link";

const HEALTH_COLOR: Record<string, string> = {
  healthy: "#16a34a",
  warning: "#d97706",
  critical: "#dc2626",
};

export default function NodeCard({ n }: { n: DashboardNode }) {
  if (!n.has_metrics || !n.metrics) {
    return (
      <div className="rounded-lg border p-4">
        <div className="font-medium">{n.hostname}</div>
        <div className="text-sm text-gray-500 mt-1">
          Registered, waiting for first Prometheus scrape…
        </div>
      </div>
    );
  }

  const m = n.metrics;
  return (
  <Link
    href={`/nodes/${encodeURIComponent(n.instance)}`}
    className="block rounded-lg border p-4 space-y-3 hover:shadow-md transition-shadow"
  >
    <div className="flex justify-between items-center">
      <div className="font-medium">{n.hostname}</div>
      <span
        className="text-xs px-2 py-0.5 rounded"
        style={{
          background: HEALTH_COLOR[m.health] + "22",
          color: HEALTH_COLOR[m.health],
        }}
      >
        {m.health}
      </span>
    </div>

    <div className="text-sm">
      {m.status === "up" ? "Online" : "Offline"}
    </div>

    <div className="grid grid-cols-3 gap-2 text-center">
      <div>
        <div className="text-xs text-gray-500">CPU</div>
        <div className="font-mono">{m.cpu_percent}%</div>
        <Sparkline
          id={`${n.instance}:cpu`}
          value={m.cpu_percent}
          color="#2563eb"
        />
      </div>

      <div>
        <div className="text-xs text-gray-500">Memory</div>
        <div className="font-mono">{m.memory_percent}%</div>
        <Sparkline
          id={`${n.instance}:mem`}
          value={m.memory_percent}
          color="#7c3aed"
        />
      </div>

      <div>
        <div className="text-xs text-gray-500">Disk</div>
        <div className="font-mono">{m.disk_percent}%</div>
        <Sparkline
          id={`${n.instance}:disk`}
          value={m.disk_percent}
          color="#ea580c"
        />
      </div>
    </div>

    <div className="grid grid-cols-2 gap-1 text-xs text-gray-600">
      <div>Load: {m.load1} / {m.load5} / {m.load15}</div>
      <div>Uptime: {m.uptime}</div>
      <div>Disk R/W: {m.disk_read} · {m.disk_write}</div>
      <div>Net RX/TX: {m.network_rx} · {m.network_tx}</div>
    </div>
  </Link>
  );
}