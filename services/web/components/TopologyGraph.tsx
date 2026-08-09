"use client";

import { useCallback, useMemo } from "react";
import dynamic from "next/dynamic";
import useSWR from "swr";
import type { TopologyEdge, TopologyGraph as TopologyGraphData, TopologyVertex } from "@/lib/types";
import { EDGE_DASH, vertexColor, vertexDisplayName } from "@/lib/topology";
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

export default function TopologyGraph({ onSelectVertex }: { onSelectVertex: (id: string) => void }) {
  const { data, error, isLoading } = useSWR<TopologyGraphData>("/api/topology", fetcher, {
    refreshInterval: 30000,
  });

  const graphData = useMemo(
    () => ({
      nodes: (data?.nodes ?? []) as GraphNode[],
      links: (data?.edges ?? []) as GraphLink[],
    }),
    [data]
  );

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

  const paintNode = useCallback((node: GraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const label = vertexDisplayName(node);
    const color = resolveCssVar(vertexColor(node));
    const isServiceOrNode = node.label === "Node" || node.label === "Service";
    const r = isServiceOrNode ? 6 : 4.5;

    ctx.beginPath();
    if (node.label === "Network" || node.label === "Subnet") {
      // Square-ish marker for Network/Subnet vertices so they read apart
      // from the circular Node/Service markers at a glance (per the Phase
      // 6 plan's "distinct shapes/icons for Service vs Network vertices").
      ctx.rect((node.x ?? 0) - r, (node.y ?? 0) - r, r * 2, r * 2);
    } else {
      ctx.arc(node.x ?? 0, node.y ?? 0, r, 0, 2 * Math.PI, false);
    }
    ctx.fillStyle = color;
    ctx.fill();

    if (globalScale > 1.2) {
      const fontSize = 11 / globalScale;
      ctx.font = `${fontSize}px Inter, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = resolveCssVar("var(--text-dim)");
      ctx.fillText(label, node.x ?? 0, (node.y ?? 0) + r + 2);
    }
  }, [resolveCssVar]);

  if (isLoading) {
    return (
      <Card className="flex h-[560px] items-center justify-center text-sm text-text-faint">
        Loading topology graph…
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="flex h-[560px] items-center justify-center text-sm">
        <span style={{ color: "var(--crit)" }}>Couldn&apos;t load the topology graph.</span>
      </Card>
    );
  }

  if (graphData.nodes.length === 0) {
    return (
      <Card className="flex h-[560px] items-center justify-center text-center text-sm text-text-faint">
        No topology data yet -- waiting for the first OpenStack sync pass.
      </Card>
    );
  }

  return (
    <Card padding="p-0" className="h-[560px] overflow-hidden">
      <ForceGraph2D
        graphData={graphData}
        nodeId="id"
        nodeLabel={(n: GraphNode) => `${vertexDisplayName(n)} (${n.label})`}
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node: GraphNode, color: string, ctx: CanvasRenderingContext2D) => {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(node.x ?? 0, node.y ?? 0, 8, 0, 2 * Math.PI, false);
          ctx.fill();
        }}
        linkColor={() => resolveCssVar("var(--border-soft)")}
        linkLineDash={(link: GraphLink) => EDGE_DASH[link.type] ?? []}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        onNodeClick={(node: GraphNode) => onSelectVertex(node.id)}
        cooldownTicks={100}
        backgroundColor="transparent"
      />
    </Card>
  );
}
