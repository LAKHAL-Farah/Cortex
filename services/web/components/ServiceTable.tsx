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

/** Notion-style dense table: sticky header row, hairline row dividers,
 * whole row clickable to open the detail panel except the "hosted on" tag,
 * which stops propagation to navigate instead. Column layout mirrors
 * NodeTable.tsx's grid-based rows. */
export default function ServiceTable({ services, onOpen }: { services: ServiceRow[]; onOpen: (id: string) => void }) {
  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[2fr_1fr_1.4fr_1fr_1fr_1fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Service</div>
        <div>Source</div>
        <div>Hosted on</div>
        <div>Zone</div>
        <div>Status</div>
        <div className="text-right">State</div>
      </div>

      <div>
        {services.map((s) => {
          const state = s.state ?? "unknown";
          const stateColor = SERVICE_STATE_COLOR[state] ?? "var(--text-muted)";
          const stateSoft = SERVICE_STATE_SOFT[state] ?? "var(--canvas)";
          const stateLabel = SERVICE_STATE_LABEL[state] ?? "Unknown";
          const source = s.source ?? "";
          const sourceColor = SERVICE_SOURCE_COLOR[source] ?? "var(--chart-2)";
          const SourceIcon = SERVICE_SOURCE_ICON[source] ?? Boxes;
          const hostTagColor = s.hostNode ? tagColorForKey(s.hostNode.id) : "var(--text-muted)";

          return (
            <button
              key={s.id}
              onClick={() => onOpen(s.id)}
              className="group grid w-full gap-4 border-b p-5 text-left transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[2fr_1fr_1.4fr_1fr_1fr_1fr]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <div className="flex min-w-0 items-center gap-3">
                {/* Minimal per-source "asset" icon, same square-badge
                    pattern as NetworkEntityTable.tsx's Icon cell, so the
                    two list tables read consistently. */}
                <span
                  className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                  style={{ background: `color-mix(in srgb, ${sourceColor} 14%, transparent)` }}
                >
                  <SourceIcon className="h-4 w-4" style={{ color: sourceColor }} strokeWidth={1.75} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-medium text-color-text">{s.binary ?? s.id}</div>
                  <div className="mt-0.5 truncate text-sm text-text-faint">{s.id}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <span className="text-sm text-text-dim">{SERVICE_SOURCE_LABEL[s.source ?? ""] ?? s.source ?? "—"}</span>
              </div>

              <div className="flex items-center">
                {s.hostNode ? (
                  <Link
                    href={`/nodes/${encodeURIComponent(s.hostNode.id)}`}
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex max-w-full items-center gap-1.5 truncate rounded-full px-2.5 py-1 text-xs font-medium transition-opacity hover:opacity-80"
                    style={{ color: hostTagColor, background: `color-mix(in srgb, ${hostTagColor} 12%, transparent)` }}
                  >
                    <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: hostTagColor }} />
                    <span className="truncate">{s.hostNode.name}</span>
                    <ArrowUpRight className="h-3 w-3 shrink-0" strokeWidth={2} />
                  </Link>
                ) : (
                  <span
                    className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                    style={{ color: "var(--text-muted)", background: "var(--canvas)" }}
                  >
                    <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: "var(--text-muted)" }} />
                    unresolved
                  </span>
                )}
              </div>

              <div className="hidden items-center sm:flex">
                <span className="text-sm text-text-dim">{s.zone ?? "—"}</span>
              </div>

              <div className="hidden items-center sm:flex">
                <span className="text-sm text-text-dim">{s.status ?? "—"}</span>
              </div>

              <div className="flex items-center justify-end gap-2">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                  style={{ color: stateColor, background: stateSoft }}
                  title={s.lastSyncedAt ? `synced ${formatRelative(s.lastSyncedAt)}` : "never synced"}
                >
                  <span className="status-dot" style={{ background: stateColor }} />
                  {stateLabel}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
