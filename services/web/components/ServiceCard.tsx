"use client";

import Link from "next/link";
import { ArrowUpRight, Boxes, Server } from "lucide-react";
import type { ServiceRow } from "@/lib/entities";
import { SERVICE_SOURCE_LABEL, SERVICE_STATE_COLOR, SERVICE_STATE_LABEL, SERVICE_STATE_SOFT } from "@/lib/entities";
import { formatRelative } from "@/lib/anomalies";
import { Card } from "./ui/Card";

export default function ServiceCard({ service, onOpen }: { service: ServiceRow; onOpen: (id: string) => void }) {
  const state = service.state ?? "unknown";
  const stateColor = SERVICE_STATE_COLOR[state] ?? "var(--text-muted)";
  const stateSoft = SERVICE_STATE_SOFT[state] ?? "var(--canvas)";
  const stateLabel = SERVICE_STATE_LABEL[state] ?? "Unknown";
  const sourceColor = "var(--chart-2)"; // matches LABEL_COLOR.Service in lib/topology.ts

  return (
    <Card interactive padding="p-0" className="relative overflow-hidden">
      <button onClick={() => onOpen(service.id)} className="block w-full text-left">
        <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: sourceColor }} />
        <div className="p-4 pl-5">
          <div className="flex items-start justify-between gap-3">
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide"
              style={{ color: sourceColor, background: "color-mix(in srgb, var(--chart-2) 12%, transparent)" }}
            >
              <Boxes className="h-3 w-3" strokeWidth={2.25} />
              {SERVICE_SOURCE_LABEL[service.source ?? ""] ?? service.source ?? "service"}
            </span>
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium"
              style={{ color: stateColor, background: stateSoft }}
            >
              <span className="status-dot" style={{ background: stateColor }} />
              {stateLabel}
            </span>
          </div>

          <div className="font-display mt-3 truncate text-[17px] font-semibold text-color-text">
            {service.binary ?? service.id}
          </div>
          <div className="mt-0.5 truncate text-sm text-text-faint">
            {service.host}
            {service.backend ? ` · ${service.backend}` : ""}
            {service.zone ? ` · ${service.zone}` : ""}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span
              className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium"
              style={{ background: "var(--canvas)", color: "var(--text-dim)" }}
            >
              {service.status ?? "—"}
            </span>
            {service.serves.length > 0 && (
              <span
                className="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium"
                style={{ background: "var(--canvas)", color: "var(--text-dim)" }}
              >
                serves {service.serves.length} {service.serves.length === 1 ? "vertex" : "vertices"}
              </span>
            )}
          </div>

          <div className="mt-4 flex items-center justify-between border-t pt-3 text-xs text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
            {service.hostNode ? (
              <Link
                href={`/nodes/${encodeURIComponent(service.hostNode.id)}`}
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-1.5 font-medium transition-colors hover:text-[var(--accent)]"
                style={{ color: "var(--text-dim)" }}
              >
                <Server className="h-3.5 w-3.5" strokeWidth={2} />
                {service.hostNode.name}
                <ArrowUpRight className="h-3 w-3" strokeWidth={2} />
              </Link>
            ) : (
              <span className="inline-flex items-center gap-1.5">
                <Server className="h-3.5 w-3.5" strokeWidth={2} />
                unresolved host
              </span>
            )}
            <span>{service.lastSyncedAt ? `synced ${formatRelative(service.lastSyncedAt)}` : "never synced"}</span>
          </div>
        </div>
      </button>
    </Card>
  );
}
