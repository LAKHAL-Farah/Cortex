"use client";

import { Waypoints } from "lucide-react";
import type { NetworkEntityDisplayRow } from "@/lib/entities";
import { NEUTRON_STATUS_COLOR, NEUTRON_STATUS_SOFT } from "@/lib/entities";
import { LABEL_COLOR, vertexIcon } from "@/lib/topology";
import { formatRelative } from "@/lib/anomalies";
import { Card } from "./ui/Card";

/** A relation chip: shows a related vertex's own label + name (e.g. "Network
 * default-ext" on a Subnet's card) so the "which net does this belong to"
 * relationship the user asked for reads at a glance, without needing a
 * per-entity detail page to link to (only :Node has one -- see
 * TopologyDetailPanel.tsx's detailHref()). */
function RelationChip({ label, name, vertexLabel }: { label: string; name: string; vertexLabel: string | null }) {
  const color = vertexLabel && vertexLabel !== "Node" ? LABEL_COLOR[vertexLabel as keyof typeof LABEL_COLOR] ?? "var(--text-muted)" : "var(--text-muted)";
  return (
    <span
      className="inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
      style={{ background: "var(--canvas)", color: "var(--text-dim)" }}
      title={`${label}: ${name}`}
    >
      <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ background: color }} />
      <span className="text-text-faint">{label}:</span>
      <span className="truncate">{name}</span>
    </span>
  );
}

export default function NetworkEntityCard({
  row,
  onOpen,
  onOpenDiagram,
}: {
  row: NetworkEntityDisplayRow;
  onOpen: (id: string) => void;
  /** Only ever passed for label === "Network" rows -- see
   * NetworkEntityView.tsx. A diagram only makes sense scoped to one
   * network (see graph_db.fetch_network_topology), not a subnet/router/
   * floating IP/instance/port in isolation. */
  onOpenDiagram?: (id: string) => void;
}) {
  const color = LABEL_COLOR[row.label];
  const Icon = vertexIcon({ label: row.label, properties: {} });
  const statusColor = row.status ? NEUTRON_STATUS_COLOR[row.status] ?? "var(--text-muted)" : null;
  const statusSoft = row.status ? NEUTRON_STATUS_SOFT[row.status] ?? "var(--canvas)" : null;

  return (
    <Card interactive padding="p-0" className="relative overflow-hidden">
      {/* A plain <div> here, not <button>, specifically so the "View
          diagram" trigger below can be a real, valid, independently
          clickable <button> rather than an invalid button-inside-a-button
          (or a non-button element faking click behavior via
          stopPropagation, which is what nesting it inside an actual
          <button> would have required). */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => onOpen(row.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen(row.id);
          }
        }}
        className="block w-full cursor-pointer text-left"
      >
        <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: color }} />
        <div className="p-4 pl-5">
          <div className="flex items-start justify-between gap-3">
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide"
              style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}
            >
              {/* vertexIcon() looks up a static, already-declared lucide-react
                  component (see lib/topology.ts), same as TopologyDetailPanel.tsx. */}
              {/* eslint-disable-next-line react-hooks/static-components */}
              <Icon className="h-3 w-3" strokeWidth={2.25} />
              {row.label}
            </span>
            {row.status && (
              <span
                className="inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium"
                style={{ color: statusColor ?? undefined, background: statusSoft ?? undefined }}
              >
                <span className="status-dot" style={{ background: statusColor ?? "var(--text-muted)" }} />
                {row.status}
              </span>
            )}
          </div>

          <div className="font-display mt-3 truncate text-[17px] font-semibold text-color-text">{row.title}</div>
          {row.subtitle && <div className="mt-0.5 truncate text-sm text-text-faint">{row.subtitle}</div>}

          {row.chips.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {row.chips.map((c) => (
                <span
                  key={c.label}
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                  style={{ background: "var(--canvas)", color: "var(--text-dim)" }}
                >
                  <span className="text-text-faint">{c.label}:</span> {c.value}
                </span>
              ))}
            </div>
          )}

          {(row.relations.some((r) => r.ref) || row.relationLists.some((r) => r.refs.length > 0)) && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {row.relations
                .filter((r) => r.ref)
                .map((r) => (
                  <RelationChip key={r.label} label={r.label} name={r.ref!.name} vertexLabel={r.ref!.label} />
                ))}
              {row.relationLists
                .filter((r) => r.refs.length > 0)
                .map((r) => (
                  <span
                    key={r.label}
                    className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                    style={{ background: "var(--canvas)", color: "var(--text-dim)" }}
                  >
                    <span className="text-text-faint">{r.label}:</span> {r.refs.length}
                  </span>
                ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 border-t px-4 pb-4 pl-5 pt-3 text-xs text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
        {onOpenDiagram ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOpenDiagram(row.id);
            }}
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ color: "var(--accent)" }}
          >
            <Waypoints className="h-3.5 w-3.5" strokeWidth={2} />
            View diagram
          </button>
        ) : (
          <span />
        )}
        <span>{row.lastSyncedAt ? `synced ${formatRelative(row.lastSyncedAt)}` : "never synced"}</span>
      </div>
    </Card>
  );
}
