"use client";

import { ShieldCheck, Cpu, HardDrive, Activity, Bot } from "lucide-react";

// This graphic is a deliberately simplified, animated stand-in for the real
// canvas topology graph in TopologyGraph.tsx -- same shape language (pointy
// hexagons, "all hexagon" per that file's comment), same per-role colors
// (--role-controller/compute/storage/monitoring from globals.css), same
// role->icon mapping as NODE_ROLE_ICON in lib/topology.ts. It stays a plain
// SVG rather than mounting react-force-graph-2d because the hero needs to
// render instantly on marketing traffic without pulling in a canvas graph
// library or a live /api/topology fetch.
const AGENTS = [
  { id: "orchestrator", x: 230, y: 216, r: 30, role: "core", label: "Orchestrator", Icon: Bot, color: "var(--accent)" },
  { id: "monitoring", x: 96, y: 108, r: 20, role: "monitoring", label: "Monitoring", Icon: Activity, color: "var(--role-monitoring)" },
  { id: "prediction", x: 366, y: 108, r: 20, role: "controller", label: "Prediction", Icon: ShieldCheck, color: "var(--role-controller)" },
  { id: "security", x: 84, y: 328, r: 20, role: "compute", label: "Security", Icon: Cpu, color: "var(--role-compute)" },
  { id: "network", x: 378, y: 328, r: 20, role: "storage", label: "Network", Icon: HardDrive, color: "var(--role-storage)" },
];

const EDGES: [string, string][] = [
  ["orchestrator", "monitoring"],
  ["orchestrator", "prediction"],
  ["orchestrator", "security"],
  ["orchestrator", "network"],
  ["monitoring", "security"],
];

function hexPoints(cx: number, cy: number, r: number) {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 3) * i - Math.PI / 2;
    return `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;
  }).join(" ");
}

export default function HexTopologyHero() {
  return (
    <div className="relative h-full w-full">
      <svg viewBox="0 0 460 420" className="h-full w-full" role="img" aria-label="Cortex orchestrator routing questions to specialist agents">
        {EDGES.map(([a, b], i) => {
          const A = AGENTS.find((n) => n.id === a)!;
          const B = AGENTS.find((n) => n.id === b)!;
          return (
            <g key={`${a}-${b}`}>
              <line x1={A.x} y1={A.y} x2={B.x} y2={B.y} stroke="var(--border)" strokeWidth={1.4} />
              <line
                x1={A.x}
                y1={A.y}
                x2={B.x}
                y2={B.y}
                stroke={B.color}
                strokeWidth={1.6}
                strokeDasharray="3 7"
                opacity={0.7}
                style={{ animation: `landing-edge-flow 2.6s linear infinite`, animationDelay: `${i * 0.25}s` }}
              />
            </g>
          );
        })}

        {AGENTS.map((n, i) => (
          <g
            key={n.id}
            style={{
              transformOrigin: `${n.x}px ${n.y}px`,
              animation: `landing-node-bob 6s ease-in-out infinite`,
              animationDelay: `${i * -1.1}s`,
            }}
          >
            <polygon
              points={hexPoints(n.x, n.y, n.r + 9)}
              fill={n.color}
              opacity={0.12}
            />
            <polygon
              points={hexPoints(n.x, n.y, n.r)}
              fill="var(--surface)"
              stroke={n.color}
              strokeWidth={1.8}
            />
            <foreignObject x={n.x - 10} y={n.y - 10} width={20} height={20}>
              <n.Icon size={20} strokeWidth={1.75} color={n.color} />
            </foreignObject>
            <text
              x={n.x}
              y={n.y + n.r + 17}
              textAnchor="middle"
              fontFamily="var(--font-mono)"
              fontSize="9.5"
              letterSpacing="0.02em"
              fill="var(--text-muted)"
            >
              {n.label}
            </text>
          </g>
        ))}
      </svg>

      <style jsx>{`
        @keyframes landing-node-bob {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-5px); }
        }
        @keyframes landing-edge-flow {
          to { stroke-dashoffset: -100; }
        }
      `}</style>
    </div>
  );
}
