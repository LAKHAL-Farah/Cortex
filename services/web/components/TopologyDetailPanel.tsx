"use client";

import useSWR from "swr";
import Link from "next/link";
import { X, ArrowUpRight, ArrowDownLeft } from "lucide-react";
import type { TopologyVertexDetail } from "@/lib/types";
import { Card } from "./ui/Card";
import { EDGE_LABEL, vertexColor, vertexDisplayName } from "@/lib/topology";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

/** id -> href for "open the full page" on vertices that have one; every
 * other label only exists inside the topology graph today, so it stays a
 * plain (non-linked) row in the panel. */
function detailHref(vertex: Pick<TopologyVertexDetail, "id" | "label">): string | null {
  if (vertex.label === "Node") return `/nodes/${encodeURIComponent(vertex.id)}`;
  return null;
}

export default function TopologyDetailPanel({ vertexId, onClose }: { vertexId: string; onClose: () => void }) {
  const { data, error, isLoading } = useSWR<TopologyVertexDetail>(
    `/api/topology/nodes/${encodeURIComponent(vertexId)}`,
    fetcher
  );

  const color = data ? vertexColor(data) : "var(--accent)";
  const href = data ? detailHref(data) : null;

  return (
    <Card padding="p-0" className="relative flex h-full w-[320px] shrink-0 flex-col overflow-hidden">
      <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: color }} />
      <div className="flex items-center justify-between gap-3 border-b p-4 pl-5" style={{ borderColor: "var(--border-soft)" }}>
        <div className="min-w-0">
          {data && (
            <span
              className="inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide"
              style={{ color, background: "var(--canvas)" }}
            >
              {data.label}
            </span>
          )}
          <div className="font-display mt-2 truncate text-[15px] font-semibold text-color-text">
            {data ? vertexDisplayName(data) : vertexId}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Close vertex detail"
          className="rounded-[var(--radius-control)] p-1 text-text-faint hover:text-color-text"
        >
          <X className="h-4 w-4" strokeWidth={2} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 pl-5">
        {isLoading && <div className="text-sm text-text-faint">Loading…</div>}
        {error && <div className="text-sm" style={{ color: "var(--crit)" }}>Couldn&apos;t load this vertex.</div>}

        {data && (
          <>
            <div className="eyebrow mb-2">Properties</div>
            <dl className="mb-5 space-y-1.5 text-sm">
              {Object.entries(data.properties)
                .filter(([, v]) => v !== null && v !== undefined && v !== "")
                .map(([key, value]) => (
                  <div key={key} className="flex items-start justify-between gap-3">
                    <dt className="text-text-faint">{key}</dt>
                    <dd className="truncate text-right text-color-text">{String(value)}</dd>
                  </div>
                ))}
            </dl>

            <div className="eyebrow mb-2">Neighbors ({data.neighbors.length})</div>
            <ul className="space-y-1.5">
              {data.neighbors.map((n, i) => (
                <li
                  key={`${n.direction}-${n.relationship}-${n.id ?? i}`}
                  className="flex items-center gap-2 rounded-[var(--radius-control)] px-2 py-1.5 text-sm"
                  style={{ background: "var(--canvas)" }}
                >
                  {n.direction === "outgoing" ? (
                    <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-text-faint" strokeWidth={2} />
                  ) : (
                    <ArrowDownLeft className="h-3.5 w-3.5 shrink-0 text-text-faint" strokeWidth={2} />
                  )}
                  <span className="truncate text-color-text">{n.id ?? "?"}</span>
                  <span className="ml-auto shrink-0 text-xs text-text-faint">
                    {EDGE_LABEL[n.relationship] ?? n.relationship}
                  </span>
                </li>
              ))}
              {data.neighbors.length === 0 && <li className="text-sm text-text-faint">No connected vertices.</li>}
            </ul>

            {href && (
              <Link
                href={href}
                className="mt-5 inline-flex items-center gap-1.5 text-sm font-medium"
                style={{ color: "var(--accent)" }}
              >
                Open full node page
                <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
              </Link>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
