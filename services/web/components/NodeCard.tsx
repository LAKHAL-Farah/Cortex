import Sparkline from "./Sparkline";
import type { DashboardNode } from "@/lib/types";
import Link from "next/link";
import { Server } from "lucide-react";
import { Card } from "./ui/Card";

const HEALTH_COLOR: Record<string, string> = {
  healthy: "#16a34a",
  warning: "#d97706",
  critical: "#dc2626",
};

export default function NodeCard({ n }: { n: DashboardNode }) {
  const nodePathValue = ((n.instance && n.instance.trim()) || n.id || n.hostname || n.ip_address || "").trim();
  const encodedNodePath = nodePathValue ? encodeURIComponent(nodePathValue) : "";
  const href = encodedNodePath ? `/nodes/${encodedNodePath}` : "/nodes";

  if (!n.has_metrics || !n.metrics) {
    return (
      <Link href={href} className="block">
        <Card className="transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-bg">
              <Server className="w-5 h-5 text-text-dim" />
            </div>
            <div>
              <div className="text-base font-semibold text-color-text">{n.hostname}</div>
              <div className="text-sm text-text-faint">{n.instance}</div>
            </div>
          </div>
          <div className="text-sm text-text-faint mt-4">Registered, waiting for first Prometheus scrape…</div>
        </Card>
      </Link>
    );
  }

  const m = n.metrics;
  return (
    <Link href={href} className="block">
      <Card className="transition-shadow hover:shadow-lg">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-3xl bg-bg">
              <Server className="w-5 h-5 text-text-dim" />
            </div>
            <div>
              <div className="text-base font-semibold text-color-text">{n.hostname}</div>
              <div className="text-sm text-text-faint">{n.instance}</div>
            </div>
          </div>

          <div className="flex flex-col items-end gap-2">
            <span className="text-xs uppercase tracking-[0.12em] text-text-faint">{m.status === 'up' ? 'Online' : 'Offline'}</span>
            <span className="rounded-full px-3 py-1 text-xs font-semibold" style={{ background: HEALTH_COLOR[m.health] + '22', color: HEALTH_COLOR[m.health] }}>{m.health}</span>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 mt-5 text-center">
          <div className="rounded-[18px] bg-bg p-4">
            <div className="text-xs text-text-faint">CPU</div>
            <div className="text-2xl font-semibold mt-2">{m.cpu_percent}%</div>
            <Sparkline id={`${n.instance}:cpu`} value={m.cpu_percent} color="rgb(11,110,153)" />
          </div>

          <div className="rounded-[18px] bg-bg p-4">
            <div className="text-xs text-text-faint">Memory</div>
            <div className="text-2xl font-semibold mt-2">{m.memory_percent}%</div>
            <Sparkline id={`${n.instance}:mem`} value={m.memory_percent} color="rgb(107,33,168)" />
          </div>

          <div className="rounded-[18px] bg-bg p-4">
            <div className="text-xs text-text-faint">Disk</div>
            <div className="text-2xl font-semibold mt-2">{m.disk_percent}%</div>
            <Sparkline id={`${n.instance}:disk`} value={m.disk_percent} color="rgb(249,115,22)" />
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 text-xs text-text-faint mt-4 sm:grid-cols-2">
          <div className="rounded-[18px] bg-bg p-3">Load: {m.load1} / {m.load5} / {m.load15}</div>
          <div className="rounded-[18px] bg-bg p-3">Uptime: {m.uptime}</div>
          <div className="rounded-[18px] bg-bg p-3">Disk R/W: {m.disk_read} · {m.disk_write}</div>
          <div className="rounded-[18px] bg-bg p-3">Net RX/TX: {m.network_rx} · {m.network_tx}</div>
        </div>
      </Card>
    </Link>
  );
}