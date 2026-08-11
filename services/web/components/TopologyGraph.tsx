"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import useSWR from "swr";
// Type-only import -- erased at compile time, so pulling it in doesn't
// drag react-force-graph-2d's canvas-touching runtime code into the SSR
// bundle the way a value import would (see the dynamic() call below).
import type { ForceGraphMethods } from "react-force-graph-2d";
import {
  LocateFixed,
  Maximize2,
  Minimize2,
  Search,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { TopologyEdge, TopologyGraph as TopologyGraphData, TopologyVertex, TopologyVertexLabel } from "@/lib/types";
import {
  EDGE_COLOR,
  EDGE_DASH,
  EDGE_LABEL,
  EDGE_PARTICLES,
  VERTEX_LABELS,
  vertexColor,
  vertexDisplayName,
  vertexGlyph,
  vertexIcon,
  vertexMatchesQuery,
  vertexStatusText,
  type VertexGlyph,
} from "@/lib/topology";
import { Card } from "./ui/Card";

// react-force-graph-2d renders to a <canvas> via window/HTMLCanvasElement,
// so it can't run during SSR/prerender -- same reason PlotlyChart.tsx
// (the other canvas-ish viz in this app) is loaded the same way.
// The dynamic import loses react-force-graph-2d's generic <NodeObject,
// LinkObject> parameters, so the component itself is typed loosely here;
// the callbacks below are still fully typed against GraphNode/GraphLink,
// which is what actually matters for catching mistakes in this file.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false }) as any;

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

// force-graph's own node/link shape, decorated with the raw vertex/edge so
// the paint callbacks below don't need a second lookup by id.
interface GraphNode extends TopologyVertex {
  x?: number;
  y?: number;
}
type GraphLink = TopologyEdge;

/** Pointy-top regular hexagon path, centered at (cx, cy) -- every vertex on
 * the graph gets this same silhouette (per design: "all hexagon"); the
 * glyph drawn inside it and the fill color are what tell vertices apart,
 * not the outline shape. */
function hexPath(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number) {
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 3) * i - Math.PI / 2;
    const x = cx + r * Math.cos(a);
    const y = cy + r * Math.sin(a);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

/** Small hand-rolled vector glyphs painted inside each hex node. Kept as
 * plain canvas primitives instead of rasterizing lucide's SVGs so they
 * stay crisp at any zoom level with no async image-load flash on first
 * paint. Purely decorative -- vertexGlyph()/vertexIcon() in lib/topology.ts
 * are the source of truth for *which* glyph a vertex gets. */
function drawGlyph(ctx: CanvasRenderingContext2D, glyph: VertexGlyph, cx: number, cy: number, s: number, color: string) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = Math.max(s * 0.2, 0.55);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  switch (glyph) {
    case "shield": {
      ctx.beginPath();
      ctx.moveTo(cx, cy - s);
      ctx.lineTo(cx + s * 0.85, cy - s * 0.5);
      ctx.lineTo(cx + s * 0.85, cy + s * 0.15);
      ctx.quadraticCurveTo(cx + s * 0.85, cy + s * 0.85, cx, cy + s);
      ctx.quadraticCurveTo(cx - s * 0.85, cy + s * 0.85, cx - s * 0.85, cy + s * 0.15);
      ctx.lineTo(cx - s * 0.85, cy - s * 0.5);
      ctx.closePath();
      ctx.stroke();
      break;
    }
    case "cpu": {
      const half = s * 0.55;
      ctx.strokeRect(cx - half, cy - half, half * 2, half * 2);
      [-0.5, 0, 0.5].forEach((p) => {
        ctx.beginPath();
        ctx.moveTo(cx + p * s, cy - half);
        ctx.lineTo(cx + p * s, cy - s);
        ctx.moveTo(cx + p * s, cy + half);
        ctx.lineTo(cx + p * s, cy + s);
        ctx.stroke();
      });
      break;
    }
    case "disk": {
      const rx = s * 0.75;
      const ry = s * 0.3;
      ctx.beginPath();
      ctx.ellipse(cx, cy - s * 0.35, rx, ry, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx - rx, cy - s * 0.35);
      ctx.lineTo(cx - rx, cy + s * 0.45);
      ctx.quadraticCurveTo(cx, cy + s * 0.45 + ry, cx + rx, cy + s * 0.45);
      ctx.lineTo(cx + rx, cy - s * 0.35);
      ctx.stroke();
      break;
    }
    case "pulse": {
      ctx.beginPath();
      ctx.moveTo(cx - s, cy);
      ctx.lineTo(cx - s * 0.35, cy);
      ctx.lineTo(cx - s * 0.1, cy - s * 0.8);
      ctx.lineTo(cx + s * 0.25, cy + s * 0.8);
      ctx.lineTo(cx + s * 0.5, cy);
      ctx.lineTo(cx + s, cy);
      ctx.stroke();
      break;
    }
    case "box": {
      const half = s * 0.7;
      ctx.strokeRect(cx - half, cy - half * 0.8, half * 2, half * 1.6);
      ctx.beginPath();
      ctx.moveTo(cx - half, cy - half * 0.8);
      ctx.lineTo(cx, cy - half * 0.2);
      ctx.lineTo(cx + half, cy - half * 0.8);
      ctx.moveTo(cx, cy - half * 0.2);
      ctx.lineTo(cx, cy + half * 0.8);
      ctx.stroke();
      break;
    }
    case "share": {
      const r = s * 0.2;
      const pts: [number, number][] = [
        [cx - s * 0.7, cy + s * 0.55],
        [cx + s * 0.7, cy + s * 0.55],
        [cx, cy - s * 0.7],
      ];
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      ctx.lineTo(pts[2][0], pts[2][1]);
      ctx.lineTo(pts[1][0], pts[1][1]);
      ctx.stroke();
      pts.forEach(([x, y]) => {
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      });
      break;
    }
    case "grid": {
      const half = s * 0.65;
      ctx.strokeRect(cx - half, cy - half, half * 2, half * 2);
      ctx.beginPath();
      ctx.moveTo(cx, cy - half);
      ctx.lineTo(cx, cy + half);
      ctx.moveTo(cx - half, cy);
      ctx.lineTo(cx + half, cy);
      ctx.stroke();
      break;
    }
    case "router": {
      const half = s * 0.6;
      ctx.strokeRect(cx - half, cy - half * 0.35, half * 2, half * 0.9);
      ctx.beginPath();
      ctx.moveTo(cx - half * 0.4, cy - half * 0.35);
      ctx.lineTo(cx - half * 0.6, cy - s);
      ctx.moveTo(cx + half * 0.4, cy - half * 0.35);
      ctx.lineTo(cx + half * 0.6, cy - s);
      ctx.stroke();
      break;
    }
    case "server": {
      const half = s * 0.65;
      ctx.strokeRect(cx - half, cy - s * 0.75, half * 2, s * 0.6);
      ctx.strokeRect(cx - half, cy + s * 0.05, half * 2, s * 0.6);
      ctx.beginPath();
      ctx.arc(cx - half * 0.55, cy - s * 0.45, s * 0.07, 0, Math.PI * 2);
      ctx.arc(cx - half * 0.55, cy + s * 0.35, s * 0.07, 0, Math.PI * 2);
      ctx.fill();
      break;
    }
    case "globe":
    default: {
      ctx.beginPath();
      ctx.arc(cx, cy, s * 0.75, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.ellipse(cx, cy, s * 0.32, s * 0.75, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx - s * 0.75, cy);
      ctx.lineTo(cx + s * 0.75, cy);
      ctx.stroke();
      break;
    }
  }
}

/** #RRGGBB -> rgba(r,g,b,alpha), used to dim/highlight edges around the
 * hovered/selected vertex without touching the canvas's globalAlpha (which
 * would also affect anything drawn after it in the same frame). Falls back
 * to the input unchanged for any color that isn't a plain hex (e.g. if a
 * theme var ever resolves to an already-functional CSS color). */
function hexToRgba(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const int = parseInt(m[1], 16);
  const r = (int >> 16) & 255;
  const g = (int >> 8) & 255;
  const b = int & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

function linkEndpointId(end: unknown): string | null {
  if (end && typeof end === "object" && "id" in end) return String((end as { id: unknown }).id);
  if (typeof end === "string") return end;
  return null;
}

// Once react-force-graph-2d has run its physics tick, a link's source/target
// have been resolved from plain ids (GraphLink's own shape) into the actual
// node objects (with x/y) they point at -- this is that post-resolution
// shape, used by the paint callbacks below. Loosely typed to match what the
// (untyped, dynamically-imported) library actually hands back.
interface RenderedLink extends Omit<TopologyEdge, "source" | "target"> {
  source: string | (GraphNode & Record<string, unknown>);
  target: string | (GraphNode & Record<string, unknown>);
}

export default function TopologyGraph({
  onSelectVertex,
  highlightIds,
}: {
  onSelectVertex: (id: string) => void;
  /** Vertex ids to highlight on load -- e.g. an Alerts incident's
   * graph_path.vertex_ids, arriving via /topology?highlight=id1,id2.
   * Dims every other vertex the same way an active search match does,
   * and auto-selects the first match so its detail panel opens without
   * the user having to click it. */
  highlightIds?: string[];
}) {
  const { data, error, isLoading } = useSWR<TopologyGraphData>("/api/topology", fetcher, {
    refreshInterval: 30000,
  });

  const fgRef = useRef<ForceGraphMethods<GraphNode, GraphLink> | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const [query, setQuery] = useState("");
  const [hiddenLabels, setHiddenLabels] = useState<Set<TopologyVertexLabel>>(new Set());
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFullscreen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  // Tracked in state (not read from the ref during render) so the hover
  // tooltip's edge-clamping below stays render-safe and re-measures
  // whenever the panel resizes (e.g. toggling fullscreen).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setContainerSize({ width: el.clientWidth, height: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fullscreen]);

  // react-force-graph mutates the link objects it's given *in place* --
  // once it ticks, each link's `source`/`target` are rewritten from plain
  // ids into resolved node object references. If we fed it objects that
  // are literally the ones inside the SWR cache (`data.edges`, which our
  // filtering below reads from), that mutation would silently corrupt our
  // own source of truth: id lookups like `nodeIds.has(e.source)` would
  // then compare a Set<string> against a node object and always miss,
  // making every edge vanish the next time this recomputes (e.g. on a
  // filter toggle) even though nothing about the actual topology changed.
  // Cloning into a private, pristine copy right after the fetch -- before
  // react-force-graph ever sees it -- keeps `data.edges` itself immune to
  // that mutation.
  const pristineEdges = useMemo(() => (data?.edges ?? []).map((e) => ({ ...e })), [data]);

  // Counts per label off the *unfiltered* response, so the filter chips'
  // numbers stay put while the user toggles labels on/off.
  const labelCounts = useMemo(() => {
    const counts = new Map<TopologyVertexLabel, number>();
    for (const n of data?.nodes ?? []) counts.set(n.label, (counts.get(n.label) ?? 0) + 1);
    return counts;
  }, [data]);

  const matchIds = useMemo(() => {
    if (!query.trim()) return null;
    const ids = new Set<string>();
    for (const n of data?.nodes ?? []) if (vertexMatchesQuery(n, query)) ids.add(n.id);
    return ids;
  }, [data, query]);

  const highlightSet = useMemo(
    () => (highlightIds && highlightIds.length > 0 ? new Set(highlightIds) : null),
    [highlightIds]
  );

  // A deep-linked highlight dims every other vertex the same way an
  // active search match does -- but a search the user actually typed
  // takes priority, since it's a more specific ask than whatever
  // incident linked here.
  const dimIds = matchIds ?? highlightSet;

  // Auto-select (and thereby open the detail panel for) the first
  // highlighted vertex that's actually on the graph, once, the first
  // time the data + highlight ids are both available -- a ref guard
  // instead of a selectedId check keeps this from re-firing and
  // stealing the user's own selection back after they click elsewhere,
  // including on SWR's periodic refetch.
  const didAutoFocusHighlight = useRef(false);
  useEffect(() => {
    if (didAutoFocusHighlight.current) return;
    if (!highlightIds || highlightIds.length === 0 || !data) return;
    const nodeIds = new Set(data.nodes.map((n) => n.id));
    const first = highlightIds.find((id) => nodeIds.has(id));
    if (!first) return;
    didAutoFocusHighlight.current = true;
    setSelectedId(first);
    onSelectVertex(first);
  }, [highlightIds, data, onSelectVertex]);

  const graphData = useMemo(() => {
    // Nodes are filtered from `data.nodes` directly (not cloned) so the
    // x/y position react-force-graph settles on survives filter toggles
    // instead of the whole layout jumping back to center each time.
    const nodes = (data?.nodes ?? []).filter((n) => !hiddenLabels.has(n.label)) as GraphNode[];
    const nodeIds = new Set(nodes.map((n) => n.id));
    // Edges, on the other hand, get a *fresh* clone every time (off the
    // untouched pristineEdges, never off data.edges) -- see the comment
    // above pristineEdges for why that clone matters here.
    const links = pristineEdges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map((e) => ({ ...e })) as GraphLink[];
    return { nodes, links };
  }, [data, pristineEdges, hiddenLabels]);

  // Canvas's fillStyle/strokeStyle expect a resolved color, not a raw
  // `var(--x)` reference the way a real CSS/inline-style property would
  // (that's why vertexColor()'s output works fine as a React style prop
  // elsewhere in this file's siblings, but not passed straight into a 2D
  // canvas context here) -- so resolve against the current theme once per
  // mount rather than re-parsing every paint call.
  const resolveCssVar = useCallback((expr: string) => {
    if (typeof window === "undefined") return "#8b909a";
    const match = /var\((--[a-zA-Z0-9-]+)\)/.exec(expr);
    if (!match) return expr;
    const value = getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim();
    return value || "#8b909a";
  }, []);

  const activeId = hoveredId ?? selectedId;

  const paintNode = useCallback(
    (node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const label = vertexDisplayName(node);
      const color = resolveCssVar(vertexColor(node));
      const isSelected = node.id === selectedId;
      const isHovered = node.id === hoveredId;
      const isDimmed = dimIds !== null && !dimIds.has(node.id);
      const r = node.label === "Node" || node.label === "Service" ? 7.5 : 6.5;

      ctx.save();
      ctx.globalAlpha = isDimmed ? 0.22 : 1;

      if (isHovered || isSelected) {
        ctx.shadowColor = color;
        ctx.shadowBlur = isHovered ? 16 : 10;
      }

      hexPath(ctx, x, y, r);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.shadowBlur = 0;

      ctx.lineWidth = isSelected ? 1.6 : 1;
      ctx.strokeStyle = isSelected ? resolveCssVar("var(--text)") : "rgba(0,0,0,0.18)";
      ctx.stroke();

      drawGlyph(ctx, vertexGlyph(node), x, y, r * 0.52, resolveCssVar("var(--bg)"));

      if (globalScale > 1.15) {
        const fontSize = 11 / globalScale;
        ctx.font = `${fontSize}px Inter, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = resolveCssVar(isDimmed ? "var(--text-muted)" : "var(--text-dim)");
        ctx.fillText(label, x, y + r + 3);
      }
      ctx.restore();
    },
    [resolveCssVar, selectedId, hoveredId, dimIds]
  );

  const linkColorFor = useCallback(
    (link: RenderedLink) => {
      const base = resolveCssVar(EDGE_COLOR[link.type as TopologyEdge["type"]]);
      if (!activeId) return hexToRgba(base, 0.85);
      const touches = linkEndpointId(link.source) === activeId || linkEndpointId(link.target) === activeId;
      return hexToRgba(base, touches ? 1 : 0.12);
    },
    [resolveCssVar, activeId]
  );

  const linkWidthFor = useCallback(
    (link: RenderedLink) => {
      if (!activeId) return 1;
      const touches = linkEndpointId(link.source) === activeId || linkEndpointId(link.target) === activeId;
      return touches ? 2.25 : 1;
    },
    [activeId]
  );

  const paintLinkTag = useCallback(
    (link: RenderedLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
      if (globalScale < 1.3) return;
      const s = link.source;
      const t = link.target;
      if (typeof s === "string" || typeof t === "string") return;
      if (s.x == null || t.x == null || t.y == null || s.y == null) return;
      const mx = (s.x + t.x) / 2;
      const my = (s.y + t.y) / 2;
      const text = EDGE_LABEL[link.type as TopologyEdge["type"]] ?? link.type;
      const fontSize = 8.5 / globalScale;
      ctx.font = `600 ${fontSize}px Inter, sans-serif`;
      const padX = 4 / globalScale;
      const padY = 2 / globalScale;
      const textWidth = ctx.measureText(text).width;
      const boxW = textWidth + padX * 2;
      const boxH = fontSize + padY * 2;
      const color = resolveCssVar(EDGE_COLOR[link.type as TopologyEdge["type"]]);

      ctx.beginPath();
      const radius = 3 / globalScale;
      ctx.roundRect(mx - boxW / 2, my - boxH / 2, boxW, boxH, radius);
      ctx.fillStyle = resolveCssVar("var(--surface)");
      ctx.fill();
      ctx.lineWidth = 1 / globalScale;
      ctx.strokeStyle = color;
      ctx.stroke();

      ctx.fillStyle = color;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(text, mx, my);
    },
    [resolveCssVar]
  );

  const handleSelect = useCallback(
    (node: GraphNode) => {
      setSelectedId(node.id);
      onSelectVertex(node.id);
    },
    [onSelectVertex]
  );

  const toggleLabel = (label: TopologyVertexLabel) => {
    setHiddenLabels((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const zoomBy = (factor: number) => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.zoom(fg.zoom() * factor, 250);
  };

  const fitView = () => fgRef.current?.zoomToFit(400, 56);

  const hoveredVertex = useMemo(
    () => (hoveredId ? graphData.nodes.find((n) => n.id === hoveredId) ?? null : null),
    [hoveredId, graphData.nodes]
  );
  const HoveredIcon = hoveredVertex ? vertexIcon(hoveredVertex) : null;

  const bodyHeight = fullscreen ? "h-[calc(100vh-2rem)]" : "h-[420px] sm:h-[520px] lg:h-[600px]";

  return (
    <>
      {fullscreen && (
        <div
          className="fixed inset-0 z-40"
          style={{ background: "rgba(10,12,16,0.55)", backdropFilter: "blur(2px)" }}
          onClick={() => setFullscreen(false)}
        />
      )}
      <Card
        padding="p-0"
        className={`relative overflow-hidden ${fullscreen ? "fixed inset-4 z-50 flex flex-col" : ""}`}
      >
        {/* Toolbar: search, per-label filter chips, zoom + fullscreen controls. */}
        <div
          className="flex flex-wrap items-center gap-2 border-b p-3"
          style={{ borderColor: "var(--border-soft)" }}
        >
          <div className="relative min-w-[180px] flex-1 sm:flex-none sm:w-56">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" strokeWidth={2} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search vertices…"
              className="w-full rounded-[var(--radius-control)] py-1.5 pl-8 pr-7 text-xs text-color-text outline-none transition-colors"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                aria-label="Clear search"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-faint hover:text-color-text"
              >
                <X className="h-3 w-3" strokeWidth={2} />
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {VERTEX_LABELS.filter((l) => (labelCounts.get(l) ?? 0) > 0).map((label) => {
              const active = !hiddenLabels.has(label);
              const Icon = vertexIcon({ label, properties: {} });
              return (
                <button
                  key={label}
                  onClick={() => toggleLabel(label)}
                  className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium transition-colors"
                  style={{
                    border: "1px solid var(--border)",
                    background: active ? "var(--canvas)" : "transparent",
                    color: active ? "var(--text-dim)" : "var(--text-muted)",
                    opacity: active ? 1 : 0.55,
                  }}
                  title={active ? `Hide ${label} vertices` : `Show ${label} vertices`}
                >
                  <Icon className="h-3 w-3" strokeWidth={2} />
                  {label}
                  <span className="stat-figure text-text-muted">{labelCounts.get(label)}</span>
                </button>
              );
            })}
          </div>

          <div className="ml-auto flex items-center gap-1">
            <button onClick={() => zoomBy(1.35)} aria-label="Zoom in" className="rounded-[var(--radius-control)] p-1.5 text-text-faint hover:text-color-text hover:bg-[var(--canvas)]">
              <ZoomIn className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
            <button onClick={() => zoomBy(1 / 1.35)} aria-label="Zoom out" className="rounded-[var(--radius-control)] p-1.5 text-text-faint hover:text-color-text hover:bg-[var(--canvas)]">
              <ZoomOut className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
            <button onClick={fitView} aria-label="Fit to view" className="rounded-[var(--radius-control)] p-1.5 text-text-faint hover:text-color-text hover:bg-[var(--canvas)]">
              <LocateFixed className="h-3.5 w-3.5" strokeWidth={2} />
            </button>
            <button
              onClick={() => setFullscreen((v) => !v)}
              aria-label={fullscreen ? "Exit fullscreen" : "Expand to fullscreen"}
              className="rounded-[var(--radius-control)] p-1.5 text-text-faint hover:text-color-text hover:bg-[var(--canvas)]"
            >
              {fullscreen ? <Minimize2 className="h-3.5 w-3.5" strokeWidth={2} /> : <Maximize2 className="h-3.5 w-3.5" strokeWidth={2} />}
            </button>
          </div>
        </div>

        {/* Faint dot-grid backdrop -- a nod to dependency-graph UIs, not an
            ambient glow wash, so it stays out of the way of the graph itself. */}
        <div
          ref={containerRef}
          className={`relative ${fullscreen ? "flex-1" : bodyHeight} overflow-hidden`}
          style={{
            backgroundImage: "radial-gradient(color-mix(in srgb, var(--text-muted) 28%, transparent) 1px, transparent 1px)",
            backgroundSize: "22px 22px",
            backgroundColor: "var(--surface)",
          }}
          onMouseMove={(e) => {
            const rect = containerRef.current?.getBoundingClientRect();
            if (!rect) return;
            setMousePos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
          }}
          onMouseLeave={() => setHoveredId(null)}
        >
          {isLoading && (
            <div className="flex h-full items-center justify-center text-sm text-text-faint">Loading topology graph…</div>
          )}

          {error && !isLoading && (
            <div className="flex h-full items-center justify-center text-sm">
              <span style={{ color: "var(--crit)" }}>Couldn&apos;t load the topology graph.</span>
            </div>
          )}

          {!isLoading && !error && (data?.nodes.length ?? 0) === 0 && (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-text-faint">
              No topology data yet -- waiting for the first OpenStack sync pass.
            </div>
          )}

          {!isLoading && !error && (data?.nodes.length ?? 0) > 0 && graphData.nodes.length === 0 && (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-text-faint">
              Every vertex type is filtered out. Re-enable a filter above to see the graph.
            </div>
          )}

          {!isLoading && !error && graphData.nodes.length > 0 && (
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              nodeId="id"
              nodeLabel={() => ""}
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={(node: GraphNode, color: string, ctx: CanvasRenderingContext2D) => {
                ctx.fillStyle = color;
                hexPath(ctx, node.x ?? 0, node.y ?? 0, 10);
                ctx.fill();
              }}
              linkColor={linkColorFor}
              linkWidth={linkWidthFor}
              linkLineDash={(link: GraphLink) => EDGE_DASH[link.type] ?? []}
              linkDirectionalArrowLength={4}
              linkDirectionalArrowRelPos={1}
              linkDirectionalArrowColor={linkColorFor}
              linkDirectionalParticles={(link: GraphLink) => EDGE_PARTICLES[link.type] ?? 0}
              linkDirectionalParticleWidth={2}
              linkDirectionalParticleColor={linkColorFor}
              linkCanvasObjectMode={() => "after"}
              linkCanvasObject={paintLinkTag}
              onNodeClick={handleSelect}
              onNodeHover={(node: GraphNode | null) => setHoveredId(node?.id ?? null)}
              cooldownTicks={100}
              backgroundColor="transparent"
              onEngineStop={fitView}
            />
          )}

          {hoveredVertex && mousePos && HoveredIcon && (
            <div
              className="pointer-events-none absolute z-10 w-64 rounded-[var(--radius-panel)] border p-3 text-xs shadow-[var(--shadow-hover)] transition-opacity duration-100"
              style={{
                left: Math.min(mousePos.x + 16, Math.max(containerSize.width, 264) - 264),
                top: Math.min(mousePos.y + 16, Math.max(containerSize.height, 140) - 140),
                background: "var(--surface)",
                borderColor: "var(--border)",
              }}
            >
              <div className="flex items-center gap-2">
                <span
                  className="inline-flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
                  style={{ background: `color-mix(in srgb, ${vertexColor(hoveredVertex)} 16%, transparent)` }}
                >
                  {/* vertexIcon() looks up a static, already-declared lucide-react
                      component rather than creating a new one per call. */}
                  {/* eslint-disable-next-line react-hooks/static-components */}
                  <HoveredIcon className="h-3.5 w-3.5" style={{ color: vertexColor(hoveredVertex) }} strokeWidth={2.25} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-semibold text-color-text">{vertexDisplayName(hoveredVertex)}</div>
                  <div className="eyebrow" style={{ color: vertexColor(hoveredVertex) }}>
                    {hoveredVertex.label}
                    {hoveredVertex.label === "Node" && hoveredVertex.properties.role ? ` · ${hoveredVertex.properties.role}` : ""}
                  </div>
                </div>
              </div>
              {vertexStatusText(hoveredVertex) && (
                <div className="mt-2 text-text-faint">
                  Status: <span className="text-color-text">{vertexStatusText(hoveredVertex)}</span>
                </div>
              )}
              <div className="mt-2 text-[10px] text-text-muted">Click to open full details →</div>
            </div>
          )}
        </div>

        {/* Legend / relationship key, matching the icons + colors actually
            painted on the canvas above. */}
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t px-3 py-2.5 text-[11px] text-text-faint"
          style={{ borderColor: "var(--border-soft)" }}
        >
          <span className="stat-figure text-text-dim">
            {graphData.nodes.length} vertices · {graphData.links.length} edges
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-3.5" style={{ background: "var(--text-muted)" }} />
            {EDGE_LABEL.RUNS_ON}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-3.5"
              style={{ background: "repeating-linear-gradient(90deg, var(--chart-3) 0 3px, transparent 3px 5px)" }}
            />
            {EDGE_LABEL.CONNECTS}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-3.5"
              style={{ background: "repeating-linear-gradient(90deg, var(--accent) 0 4px, transparent 4px 7px)" }}
            />
            {EDGE_LABEL.SERVES} (animated)
          </span>
          <span className="ml-auto hidden sm:inline">Hover a vertex for details · click to pin it open</span>
        </div>
      </Card>
    </>
  );
}
