"use client";

import Link from "next/link";
import { ArrowUpRight, Boxes } from "lucide-react";
import type { ServiceRow } from "@/lib/entities";
import {
  SERVICE_SOURCE_COLOR,
  SERVICE_SOURCE_ICON,
  SERVICE_SOURCE_LABEL,
  SERVICE_STATE_COLOR,
  SERVICE_STATE_LABEL,
  SERVICE_STATE_SOFT,
  tagColorForKey,
} from "@/lib/entities";
import { formatRelative } from "@/lib/anomalies";
import { Card } from "./ui/Card";

export default function ServiceCard({ service, onOpen }: { service: ServiceRow; onOpen: (id: string) => void }) {
  const state = service.state ?? "unknown";
  const stateColor = SERVICE_STATE_COLOR[state] ?? "var(--text-muted)";
  const stateSoft = SERVICE_STATE_SOFT[state] ?? "var(--canvas)";
  const stateLabel = SERVICE_STATE_LABEL[state] ?? "Unknown";
  const source = service.source ?? "";
  // Per-project (Nova/Cinder/Neutron) color + icon instead of one flat
  // Service color, so the little square "asset" badge below reads as
  // compute/storage/networking at a glance -- see lib/entities.ts.
  const sourceColor = SERVICE_SOURCE_COLOR[source] ?? "var(--chart-2)";
  const SourceIcon = SERVICE_SOURCE_ICON[source] ?? Boxes;
  const hostTagColor = service.hostNode ? tagColorForKey(service.hostNode.id) : "var(--text-muted)";

  return (
    <Card interactive padding="p-0" className="relative overflow-hidden">
      <button onClick={() => onOpen(service.id)} className="block w-full text-left">
        <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: sourceColor }} />
        <div className="p-4 pl-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2.5">
              <span
                className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                style={{ background: `color-mix(in srgb, ${sourceColor} 14%, transparent)` }}
              >
                <SourceIcon className="h-4.5 w-4.5" style={{ color: sourceColor }} strokeWidth={1.75} />
              </span>
              <span
                className="inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide"
                style={{ color: sourceColor, background: `color-mix(in srgb, ${sourceColor} 12%, transparent)` }}
              >
                {SERVICE_SOURCE_LABEL[source] ?? service.source ?? "service"}
              </span>
            </div>
            <span
              className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium"
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
                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium transition-opacity hover:opacity-80"
                style={{ color: hostTagColor, background: `color-mix(in srgb, ${hostTagColor} 12%, transparent)` }}
              >
                <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: hostTagColor }} />
                {service.hostNode.name}
                <ArrowUpRight className="h-3 w-3" strokeWidth={2} />
              </Link>
            ) : (
              <span
                className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium"
                style={{ color: "var(--text-muted)", background: "var(--canvas)" }}
              >
                <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: "var(--text-muted)" }} />
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
