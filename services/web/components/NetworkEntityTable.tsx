"use client";

import type { NetworkEntityDisplayRow } from "@/lib/entities";
import { NEUTRON_STATUS_COLOR, NEUTRON_STATUS_SOFT } from "@/lib/entities";
import { LABEL_COLOR, vertexIcon } from "@/lib/topology";

/** Flattens a row's relations + relationLists into one "Related" cell: a
 * name for the singular relations (Subnet -> Network, FloatingIP ->
 * Network/Router), a "N items" chip for the plural ones (Network ->
 * subnets/routers/floating IPs/agents) -- keeps the table to one relation
 * column regardless of which entity type is being listed. */
function relatedSummary(row: NetworkEntityDisplayRow): { label: string; text: string }[] {
  const items: { label: string; text: string }[] = [];
  for (const r of row.relations) {
    if (r.ref) items.push({ label: r.label, text: r.ref.name });
  }
  for (const r of row.relationLists) {
    if (r.refs.length > 0) items.push({ label: r.label, text: String(r.refs.length) });
  }
  return items;
}

export default function NetworkEntityTable({ rows, onOpen }: { rows: NetworkEntityDisplayRow[]; onOpen: (id: string) => void }) {
  const showStatus = rows.some((r) => r.status);

  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[2fr_2fr_1fr_1fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Name</div>
        <div>Related</div>
        {showStatus && <div>Status</div>}
        <div className="text-right">Synced</div>
      </div>

      <div>
        {rows.map((row) => {
          const color = LABEL_COLOR[row.label];
          const Icon = vertexIcon({ label: row.label, properties: {} });
          const statusColor = row.status ? NEUTRON_STATUS_COLOR[row.status] ?? "var(--text-muted)" : null;
          const statusSoft = row.status ? NEUTRON_STATUS_SOFT[row.status] ?? "var(--canvas)" : null;
          const related = relatedSummary(row);

          return (
            <button
              key={row.id}
              onClick={() => onOpen(row.id)}
              className="group grid w-full gap-4 border-b p-5 text-left transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[2fr_2fr_1fr_1fr]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <div className="flex items-center gap-3 min-w-0">
                <span
                  className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                  style={{ background: `color-mix(in srgb, ${color} 14%, transparent)` }}
                >
                  <Icon className="h-4 w-4" style={{ color }} strokeWidth={1.75} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-medium text-color-text">{row.title}</div>
                  {row.subtitle && <div className="mt-0.5 truncate text-sm text-text-faint">{row.subtitle}</div>}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                {related.length === 0 && <span className="text-sm text-text-faint">—</span>}
                {related.map((r) => (
                  <span
                    key={r.label}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
                    style={{ background: "var(--canvas)", color: "var(--text-dim)" }}
                  >
                    <span className="text-text-faint">{r.label}:</span> {r.text}
                  </span>
                ))}
              </div>

              {showStatus && (
                <div className="hidden items-center sm:flex">
                  {row.status ? (
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                      style={{ color: statusColor ?? undefined, background: statusSoft ?? undefined }}
                    >
                      <span className="status-dot" style={{ background: statusColor ?? "var(--text-muted)" }} />
                      {row.status}
                    </span>
                  ) : (
                    <span className="text-sm text-text-faint">—</span>
                  )}
                </div>
              )}

              <div className="flex items-center justify-end text-sm text-text-faint">
                {row.lastSyncedAt ? new Date(row.lastSyncedAt).toLocaleString() : "—"}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
