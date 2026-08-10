"use client";

import { useEffect } from "react";
import useSWR from "swr";
import Link from "next/link";
import { motion } from "framer-motion";
import { X, ArrowUpRight, ArrowDownLeft } from "lucide-react";
import type { TopologyVertexDetail } from "@/lib/types";
import { EDGE_COLOR, EDGE_LABEL, vertexColor, vertexDisplayName, vertexIcon, vertexStatusText } from "@/lib/topology";

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
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <motion.div
        className="fixed inset-0 z-40"
        style={{ background: "rgba(10,12,16,0.45)", backdropFilter: "blur(2px)" }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      />
      <motion.aside
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[380px] flex-col overflow-hidden border-l"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 34 }}
      >
        <TopologyDetailBody vertexId={vertexId} onClose={onClose} />
      </motion.aside>
    </>
  );
}

function TopologyDetailBody({ vertexId, onClose }: { vertexId: string; onClose: () => void }) {
  const { data, error, isLoading } = useSWR<TopologyVertexDetail>(
    `/api/topology/nodes/${encodeURIComponent(vertexId)}`,
    fetcher
  );

  const color = data ? vertexColor(data) : "var(--accent)";
  const href = data ? detailHref(data) : null;
  const Icon = data ? vertexIcon(data) : null;
  const status = data ? vertexStatusText(data) : null;

  return (
    <>
      <div className="glow-surface pointer-events-none absolute inset-0 -z-10" aria-hidden="true" />
      <div className="flex items-start justify-between gap-3 border-b p-5" style={{ borderColor: "var(--border-soft)" }}>
        <div className="flex min-w-0 items-start gap-3">
          {Icon && (
            <span
              className="mt-0.5 inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
              style={{ background: `color-mix(in srgb, ${color} 14%, transparent)` }}
            >
              {/* vertexIcon() looks up a static, already-declared lucide-react
                  component (see lib/topology.ts) rather than creating a new
                  one per call, so it's safe to render despite the lint heuristic. */}
              {/* eslint-disable-next-line react-hooks/static-components */}
              <Icon className="h-5 w-5" style={{ color }} strokeWidth={2} />
            </span>
          )}
          <div className="min-w-0">
            {data && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  className="inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
                  style={{ color, background: `color-mix(in srgb, ${color} 12%, transparent)` }}
                >
                  {data.label}
                </span>
                {data.label === "Node" && typeof data.properties.role === "string" && (
                  <span className="text-[11px] font-medium capitalize text-text-faint">{data.properties.role}</span>
                )}
                {status && (
                  <span className="text-[11px] font-medium text-text-faint">· {status}</span>
                )}
              </div>
            )}
            <div className="font-display mt-1.5 truncate text-[16px] font-semibold text-color-text">
              {data ? vertexDisplayName(data) : vertexId}
            </div>
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

      <div className="flex-1 overflow-y-auto p-5">
        {isLoading && (
          <div className="grid gap-2">
            <div className="h-4 w-2/3 animate-pulse rounded" style={{ background: "var(--canvas)" }} />
            <div className="h-4 w-full animate-pulse rounded" style={{ background: "var(--canvas)" }} />
            <div className="h-4 w-1/2 animate-pulse rounded" style={{ background: "var(--canvas)" }} />
          </div>
        )}
        {error && (
          <div className="text-sm" style={{ color: "var(--crit)" }}>
            Couldn&apos;t load this vertex.
          </div>
        )}

        {data && (
          <>
            <div className="eyebrow mb-2">Properties</div>
            <dl className="mb-6 space-y-1.5 text-sm">
              {Object.entries(data.properties)
                .filter(([, v]) => v !== null && v !== undefined && v !== "")
                .map(([key, value]) => (
                  <div
                    key={key}
                    className="flex items-start justify-between gap-3 border-b py-1.5 last:border-0"
                    style={{ borderColor: "var(--border-soft)" }}
                  >
                    <dt className="text-text-faint">{key}</dt>
                    <dd className="truncate text-right text-color-text">{String(value)}</dd>
                  </div>
                ))}
            </dl>

            <div className="mb-2 flex items-center justify-between">
              <div className="eyebrow">Neighbors</div>
              <span className="stat-figure text-xs text-text-muted">{data.neighbors.length}</span>
            </div>
            <ul className="space-y-1.5">
              {data.neighbors.map((n, i) => {
                const edgeColor = EDGE_COLOR[n.relationship] ?? "var(--text-muted)";
                return (
                  <li
                    key={`${n.direction}-${n.relationship}-${n.id ?? i}`}
                    className="flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-2 text-sm transition-colors"
                    style={{ background: "var(--canvas)" }}
                  >
                    {n.direction === "outgoing" ? (
                      <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-text-faint" strokeWidth={2} />
                    ) : (
                      <ArrowDownLeft className="h-3.5 w-3.5 shrink-0 text-text-faint" strokeWidth={2} />
                    )}
                    <span className="truncate text-color-text">{n.id ?? "?"}</span>
                    <span
                      className="stat-figure ml-auto shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                      style={{ color: edgeColor, background: `color-mix(in srgb, ${edgeColor} 14%, transparent)` }}
                    >
                      {EDGE_LABEL[n.relationship] ?? n.relationship}
                    </span>
                  </li>
                );
              })}
              {data.neighbors.length === 0 && <li className="text-sm text-text-faint">No connected vertices.</li>}
            </ul>

            {href && (
              <Link
                href={href}
                className="mt-6 inline-flex items-center gap-1.5 text-sm font-medium"
                style={{ color: "var(--accent)" }}
              >
                Open full node page
                <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
              </Link>
            )}
          </>
        )}
      </div>
    </>
  );
}
