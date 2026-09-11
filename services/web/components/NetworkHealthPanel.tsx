"use client";

import useSWR from "swr";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Globe,
  Sparkles,
  RefreshCw,
  Router as RouterIcon,
  Share2,
  WifiOff,
} from "lucide-react";
import Card from "./ui/Card";
import type { NetworkHealth, NetworkLatency } from "@/lib/types";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

const STATUS_COLOR: Record<NetworkHealth["status"], string> = {
  ok: "var(--ok)",
  degraded: "var(--warn)",
};
const STATUS_SOFT: Record<NetworkHealth["status"], string> = {
  ok: "var(--ok-soft)",
  degraded: "var(--warn-soft)",
};
const STATUS_LABEL: Record<NetworkHealth["status"], string> = {
  ok: "All clear",
  degraded: "Attention needed",
};

function itemName(item: Record<string, unknown>, nameKey: string) {
  return String(item[nameKey] ?? item.name ?? item.id ?? "Unknown resource");
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function portDiagnosis(port: Record<string, unknown>) {
  const reportedReason = port.status_reason;
  if (typeof reportedReason === "string" && reportedReason.trim()) return reportedReason;

  const network = asRecord(port.network);
  const device = asRecord(port.device);
  const networkName = String(network?.name ?? port.network_id ?? "an unknown network");
  const deviceName = String(device?.hostname ?? device?.name ?? port.device_id ?? "an unbound device");
  const owner = String(port.device_owner ?? "unknown owner");
  const adminUp = port.admin_state_up === true;
  const expectedState = adminUp ? "is administratively enabled" : "is administratively disabled";
  const likelyCause = adminUp
    ? "This commonly indicates a binding, virtual-interface, or host-agent issue."
    : "Its down state is expected until an operator enables it."

  return `Neutron reports ${String(port.status ?? "an unexpected state")}; the port ${expectedState}, belongs to ${owner}, and is attached to ${deviceName} on ${networkName}. ${likelyCause}`;
}

function routerDiagnosis(router: Record<string, unknown>) {
  const reportedReason = router.status_reason;
  if (typeof reportedReason === "string" && reportedReason.trim()) return reportedReason;

  const adminState = router.admin_state_up === true ? "enabled" : "disabled";
  return `Neutron reports router status ${String(router.status ?? "unknown")} while its administrative state is ${adminState}. Check the L3 agent and the external gateway configuration before restoring service.`;
}

function floatingIpDiagnosis(floatingIp: Record<string, unknown>) {
  const reportedReason = floatingIp.status_reason;
  if (typeof reportedReason === "string" && reportedReason.trim()) return reportedReason;

  const fixedIp = floatingIp.fixed_ip_address;
  return fixedIp
    ? `This floating IP is mapped to ${String(fixedIp)} but has no associated router in Neutron. Associate it with the correct router or remove the stale allocation.`
    : "This floating IP has no associated router or fixed IP. Associate it with a port/router or release the unused allocation.";
}

function investigationPrompt(kind: string, item: Record<string, unknown>, diagnosis: string) {
  return `Investigate this Neutron ${kind} incident. Resource: ${itemName(item, kind === "floating IP" ? "floating_ip_address" : "name")}. Evidence: ${diagnosis} Explain the most likely cause, the safest verification commands, and the recommended remediation. State what is confirmed versus inferred.`;
}

function AnomalyGroup({ icon: Icon, label, items, nameKey, diagnosis, investigationKind }: {
  icon: typeof RouterIcon;
  label: string;
  items: Record<string, unknown>[];
  nameKey: string;
  diagnosis?: (item: Record<string, unknown>) => string;
  investigationKind?: string;
}) {
  if (items.length === 0) return null;

  return (
    <section className="rounded-[var(--radius-control)] border border-border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full" style={{ background: "var(--warn-soft)" }}>
          <Icon className="h-3.5 w-3.5" style={{ color: "var(--warn)" }} strokeWidth={2} />
        </span>
        <p className="text-sm font-medium text-text">{label}</p>
        <span className="ml-auto rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums" style={{ color: "var(--warn)", background: "var(--warn-soft)" }}>
          {items.length}
        </span>
      </div>
      <ul className="mt-2 space-y-1 pl-8 text-xs text-text-faint">
        {items.slice(0, 3).map((item, index) => (
          <li key={String(item.id ?? `${label}-${index}`)}>
            <p className="truncate" title={itemName(item, nameKey)}>{itemName(item, nameKey)}</p>
            {diagnosis && <p className="mt-0.5 leading-5 text-text-faint">{diagnosis(item)}</p>}
            {diagnosis && investigationKind && (
              <a
                href={`/copilot?investigate=${encodeURIComponent(investigationPrompt(investigationKind, item, diagnosis(item)))}`}
                className="mt-2 inline-flex items-center gap-1 text-xs font-semibold"
                style={{ color: "var(--accent)" }}
              >
                <Sparkles className="h-3 w-3" strokeWidth={2} />
                Investigate with Copilot
              </a>
            )}
          </li>
        ))}
        {items.length > 3 && <li>+{items.length - 3} more</li>}
      </ul>
    </section>
  );
}

function latencyLabel(entry: NetworkLatency) {
  if (!entry.reachable) return "Unreachable";
  if (entry.latency_ms === null) return "No measurement";
  return `${entry.latency_ms.toFixed(1)} ms`;
}

function latencyColor(entry: NetworkLatency) {
  if (!entry.reachable) return "var(--crit)";
  if (entry.latency_ms === null) return "var(--text-faint)";
  if (entry.latency_ms >= 100) return "var(--warn)";
  return "var(--ok)";
}

/** Compact, live view of network anomalies and node-to-node reachability. */
export default function NetworkHealthPanel() {
  const { data, error, isLoading, isValidating, mutate } = useSWR<NetworkHealth>("/api/network/health", fetcher, {
    refreshInterval: 15000,
    revalidateOnFocus: true,
  });

  if (isLoading) {
    return <Card><div className="flex items-center gap-2 text-sm text-text-faint"><RefreshCw className="h-4 w-4 animate-spin" strokeWidth={2} />Checking network health…</div></Card>;
  }

  if (error || !data) {
    return (
      <Card>
        <div className="flex flex-wrap items-center gap-3 text-sm" style={{ color: "var(--crit)" }}>
          <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />
          <span>Network health is currently unavailable.</span>
          <button onClick={() => mutate()} className="ml-auto inline-flex items-center gap-1.5 font-medium underline underline-offset-2" type="button"><RefreshCw className="h-3.5 w-3.5" strokeWidth={2} />Retry</button>
        </div>
      </Card>
    );
  }

  const color = STATUS_COLOR[data.status];
  const soft = STATUS_SOFT[data.status];
  const anomalyCount = data.routers_down.length + data.floating_ips_orphaned.length + data.ports_down.length;
  const reachableCount = data.latencies.filter((entry) => entry.reachable).length;
  const noAnomalies = anomalyCount === 0;
  const graphUnavailable = !data.graph_available;

  return (
    <Card className="overflow-hidden" padding="p-0">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-text">Network health</h3>
            <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium" style={{ color, background: soft }}>
              <span className={data.status === "ok" ? "status-dot glow-pulse" : "status-dot"} style={{ background: color, ["--pulse-color" as string]: color }} />
              {STATUS_LABEL[data.status]}
            </span>
          </div>
          <p className="mt-1 text-xs text-text-faint">Live checks refresh automatically every 15 seconds.</p>
        </div>
        <button aria-label="Refresh network health" className="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-control)] border border-border text-text-faint hover:bg-bg-hover hover:text-text" onClick={() => mutate()} type="button">
          <RefreshCw className={`h-3.5 w-3.5 ${isValidating ? "animate-spin" : ""}`} strokeWidth={2} />
        </button>
      </div>

      <div className="grid grid-cols-1 divide-y divide-border sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <div className="px-5 py-4"><p className="text-xs font-medium text-text-faint">Open issues</p><p className="mt-1 stat-figure text-2xl text-text">{anomalyCount}</p><p className="mt-1 text-xs text-text-faint">{graphUnavailable ? "Topology data is temporarily unavailable." : noAnomalies ? "No router, IP, or port anomalies." : "Requires operator attention."}</p></div>
        <div className="px-5 py-4"><p className="text-xs font-medium text-text-faint">Reachable nodes</p><p className="mt-1 stat-figure text-2xl text-text">{reachableCount}<span className="text-base text-text-faint">/{data.latencies.length}</span></p><p className="mt-1 text-xs text-text-faint">{data.latencies.length ? "From the latest TCP reachability check." : "No active nodes to measure."}</p></div>
      </div>

      <div className="grid gap-5 px-5 py-4 lg:grid-cols-2">
        <section aria-label="Network anomalies">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-text-faint"><AlertTriangle className="h-3.5 w-3.5" strokeWidth={2} />Anomalies</div>
          {graphUnavailable ? (
            <div className="flex items-center gap-2 rounded-[var(--radius-control)] border border-border px-3 py-3 text-sm" style={{ color: "var(--warn)" }}><AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={2} />Topology anomaly checks are temporarily unavailable.</div>
          ) : noAnomalies ? (
            <div className="flex items-center gap-2 rounded-[var(--radius-control)] border border-border px-3 py-3 text-sm text-text-faint"><CheckCircle2 className="h-4 w-4 shrink-0" style={{ color: "var(--ok)" }} strokeWidth={2} />No network anomalies detected.</div>
          ) : (
            <div className="space-y-2">
              <AnomalyGroup icon={RouterIcon} label="Routers down" items={data.routers_down} nameKey="name" diagnosis={routerDiagnosis} investigationKind="router" />
              <AnomalyGroup icon={Globe} label="Orphaned floating IPs" items={data.floating_ips_orphaned} nameKey="floating_ip_address" diagnosis={floatingIpDiagnosis} investigationKind="floating IP" />
              <AnomalyGroup icon={Share2} label="Ports down" items={data.ports_down} nameKey="name" diagnosis={portDiagnosis} investigationKind="port" />
            </div>
          )}
        </section>

        <section aria-label="Inter-node latency">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-text-faint"><Activity className="h-3.5 w-3.5" strokeWidth={2} />Inter-node latency</div>
          <div className="space-y-2">
            {data.latencies.map((entry) => (
              <div key={`${entry.hostname}-${entry.port}`} className="flex items-center justify-between gap-3 rounded-[var(--radius-control)] border border-border px-3 py-2">
                <div className="min-w-0"><p className="truncate text-sm font-medium text-text">{entry.hostname}</p><p className="truncate text-xs text-text-faint">{entry.ip_address ?? "No IP address"}:{entry.port}</p>{!entry.reachable && entry.error && <p className="mt-1 truncate text-xs" style={{ color: "var(--crit)" }} title={entry.error}>TCP check: {entry.error}</p>}</div>
                <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-semibold tabular-nums" style={{ color: latencyColor(entry) }} title={entry.error ?? undefined}>{!entry.reachable && <WifiOff className="h-3.5 w-3.5" strokeWidth={2} />}{latencyLabel(entry)}</span>
              </div>
            ))}
            {data.latencies.length === 0 && <p className="rounded-[var(--radius-control)] border border-border px-3 py-3 text-sm text-text-faint">No active nodes to measure.</p>}
          </div>
        </section>
      </div>
    </Card>
  );
}
