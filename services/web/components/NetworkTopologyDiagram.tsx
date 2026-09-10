"use client";

import { useEffect } from "react";
import useSWR from "swr";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  Grid2x2,
  Monitor,
  Network as NetworkIcon,
  Plug,
  Router as RouterIcon,
  X,
} from "lucide-react";
import type { TopologyDiagramPort, TopologyNetworkDiagram as TopologyNetworkDiagramData } from "@/lib/types";
import { NEUTRON_STATUS_COLOR, NEUTRON_STATUS_SOFT } from "@/lib/entities";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** One port's worth of the diagram: a VM+its port combined into one chip
 * when the port is owned by an instance (the common case), or a plainer
 * "system port" chip for a DHCP/router-owned one (see topology_sync.py's
 * Phase 6 docstring on why those get a Port vertex but no owning
 * instance). Flags a port-level problem (DOWN status or admin-disabled)
 * even when the instance itself still reads ACTIVE -- a bound-but-broken
 * port is exactly the kind of thing this diagram exists to surface. */
function PortChip({ port }: { port: TopologyDiagramPort }) {
  const instance = port.instance;
  const primaryStatus = instance?.status ?? port.status ?? null;
  const color = (primaryStatus && NEUTRON_STATUS_COLOR[primaryStatus]) || "var(--text-muted)";
  const soft = (primaryStatus && NEUTRON_STATUS_SOFT[primaryStatus]) || "var(--canvas)";
  const portHasIssue = instance !== null && (port.status !== "ACTIVE" || port.admin_state_up === false);
  const Icon = instance ? Monitor : Plug;
  const title = instance ? instance.name ?? instance.id : port.name ?? port.id;
  const subtitle = instance ? port.fixed_ip_address : port.device_owner ?? "system port";

  return (
    <div
      className="relative flex min-w-[148px] max-w-[190px] flex-col gap-1.5 rounded-[var(--radius-control)] border p-2.5"
      style={{
        borderColor: portHasIssue ? "var(--crit)" : "var(--border-soft)",
        background: instance ? soft : "var(--canvas)",
      }}
      title={instance?.hypervisor_hostname ? `Runs on ${instance.hypervisor_hostname}` : undefined}
    >
      <div className="flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5 flex-shrink-0" style={{ color: instance ? color : "var(--text-muted)" }} strokeWidth={2} />
        <span className="truncate text-xs font-semibold text-color-text">{title}</span>
        {portHasIssue && <AlertTriangle className="ml-auto h-3 w-3 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2.5} />}
      </div>
      {subtitle && <div className="truncate text-[10px] text-text-faint">{subtitle}</div>}
      {primaryStatus && (
        <span
          className="inline-flex w-fit items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
          style={{ color, background: soft }}
        >
          <span className="status-dot" style={{ background: color }} />
          {primaryStatus}
        </span>
      )}
      {portHasIssue && (
        <div className="text-[10px] font-medium" style={{ color: "var(--crit)" }}>
          Port {(port.status ?? "down").toLowerCase()}
          {port.admin_state_up === false ? ", admin disabled" : ""}
        </div>
      )}
    </div>
  );
}

function ConnectorLine() {
  return <div className="h-6 w-px" style={{ background: "var(--border)" }} aria-hidden="true" />;
}

export default function NetworkTopologyDiagram({ networkId, onClose }: { networkId: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const { data, error, isLoading } = useSWR<TopologyNetworkDiagramData>(
    `/api/topology/networks/${encodeURIComponent(networkId)}/diagram`,
    fetcher
  );

  return (
    <>
      <motion.div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(10,12,16,0.55)", backdropFilter: "blur(2px)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      />
      <motion.div
        className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-8"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      >
        <motion.div
          className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-[var(--radius-panel)] border"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}
          initial={{ scale: 0.96, y: 12 }}
          animate={{ scale: 1, y: 0 }}
          exit={{ scale: 0.96, y: 12 }}
          transition={{ type: "spring", stiffness: 340, damping: 32 }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between gap-3 border-b p-4" style={{ borderColor: "var(--border-soft)" }}>
            <div className="min-w-0">
              <div className="eyebrow">Network topology</div>
              <div className="font-display mt-1 truncate text-base font-semibold text-color-text">{data?.name ?? networkId}</div>
            </div>
            <button
              onClick={onClose}
              aria-label="Close network diagram"
              className="flex-shrink-0 rounded-[var(--radius-control)] p-1.5 text-text-faint hover:bg-[var(--canvas)] hover:text-color-text"
            >
              <X className="h-4 w-4" strokeWidth={2} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-6">
            {isLoading && <div className="py-12 text-center text-sm text-text-faint">Loading topology…</div>}
            {error && (
              <div className="py-12 text-center text-sm" style={{ color: "var(--crit)" }}>
                Couldn&apos;t load this network&apos;s topology.
              </div>
            )}

            {data && (
              <div className="flex flex-col items-center">
                <div className="flex flex-wrap items-center justify-center gap-2">
                  {data.gateway_routers.length === 0 ? (
                    <div className="rounded-full border px-3 py-1.5 text-xs text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
                      No gateway router
                    </div>
                  ) : (
                    data.gateway_routers.map((r) => (
                      <div
                        key={r.id as string}
                        className="flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium"
                        style={{
                          borderColor: "var(--chart-5)",
                          color: "var(--chart-5)",
                          background: "color-mix(in srgb, var(--chart-5) 10%, transparent)",
                        }}
                      >
                        <RouterIcon className="h-3.5 w-3.5" strokeWidth={2} />
                        {(r.name as string) ?? (r.id as string)}
                      </div>
                    ))
                  )}
                </div>

                <ConnectorLine />

                <div
                  className="flex items-center gap-2 rounded-[var(--radius-control)] border-2 px-4 py-2"
                  style={{ borderColor: "var(--chart-3)", background: "color-mix(in srgb, var(--chart-3) 10%, transparent)" }}
                >
                  <NetworkIcon className="h-4 w-4" style={{ color: "var(--chart-3)" }} strokeWidth={2} />
                  <span className="text-sm font-semibold text-color-text">{data.name ?? data.id}</span>
                  {data.status && <span className="text-xs text-text-faint">· {data.status}</span>}
                </div>

                <ConnectorLine />

                <div className="flex w-full flex-col gap-4">
                  {data.subnets.length === 0 && <div className="text-center text-sm text-text-faint">No subnets on this network.</div>}
                  {data.subnets.map((sub) => (
                    <div
                      key={sub.id}
                      className="rounded-[var(--radius-panel)] border p-3"
                      style={{ borderColor: "var(--chart-4)", background: "color-mix(in srgb, var(--chart-4) 5%, transparent)" }}
                    >
                      <div className="mb-2.5 flex items-center gap-2 text-xs font-semibold" style={{ color: "var(--chart-4)" }}>
                        <Grid2x2 className="h-3.5 w-3.5" strokeWidth={2} />
                        {sub.name ?? sub.id}
                        {sub.cidr && <span className="font-normal text-text-faint">· {sub.cidr}</span>}
                      </div>
                      {sub.ports.length === 0 ? (
                        <div className="text-xs text-text-faint">No ports on this subnet.</div>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          {sub.ports.map((port) => (
                            <PortChip key={port.id} port={port} />
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </motion.div>
      </motion.div>
    </>
  );
}
