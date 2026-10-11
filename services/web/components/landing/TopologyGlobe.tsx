import { useRef, useState } from "react";
import { useAnimationFrame, useReducedMotion } from "framer-motion";

/**
 * GitHub-homepage-style 3D globe, rendered as a real perspective projection in SVG.
 *
 * Instead of countries it plots the Cortex topology: OpenStack nodes and the
 * services running on them, wired together by RUNS_ON / SERVES / CONNECTS edges.
 * No WebGL dependency — latitude rings are static ellipses, meridians squash with
 * the rotation, and every vertex is projected through a shared camera each frame.
 */

const R = 150; // sphere radius in viewBox units
const CX = 200;
const CY = 200;
const FOV = 3.2 * R; // camera distance — mild perspective, like GitHub's globe

type Vertex = {
  id: string;
  lat: number;
  lon: number;
  kind: "node" | "service" | "observer";
  /** Marks the vertex that the demo incident is anchored to. */
  alert?: boolean;
};

// Anchored on the repo's real topology: 1 controller + 2 compute + 1 storage,
// the OpenStack services on them, and the observability plane watching it all.
const VERTICES: Vertex[] = [
  { id: "controller-01", lat: 26, lon: -14, kind: "node" },
  { id: "compute-01", lat: -6, lon: 30, kind: "node" },
  { id: "compute-02", lat: -20, lon: -48, kind: "node", alert: true },
  { id: "storage-01", lat: 44, lon: 62, kind: "node" },

  { id: "keystone", lat: 48, lon: -40, kind: "service" },
  { id: "neutron-l3", lat: 8, lon: -70, kind: "service" },
  { id: "glance", lat: 62, lon: 6, kind: "service" },
  { id: "nova-compute", lat: -34, lon: -22, kind: "service", alert: true },
  { id: "cinder-volume", lat: 22, lon: 88, kind: "service" },
  { id: "rabbitmq", lat: -44, lon: 54, kind: "service" },
  { id: "mariadb", lat: 4, lon: 108, kind: "service" },
  { id: "ovs-br-ex", lat: -58, lon: -4, kind: "service" },

  { id: "prometheus", lat: 34, lon: 148, kind: "observer" },
  { id: "loki", lat: -14, lon: 172, kind: "observer" },
  { id: "qdrant", lat: -40, lon: 128, kind: "observer" },
  { id: "grafana", lat: 60, lon: -122, kind: "observer" },
  { id: "node-exporter", lat: -62, lon: 96, kind: "observer" },
];

const EDGES: [string, string][] = [
  ["nova-compute", "compute-02"],
  ["keystone", "controller-01"],
  ["neutron-l3", "controller-01"],
  ["glance", "controller-01"],
  ["cinder-volume", "storage-01"],
  ["rabbitmq", "compute-01"],
  ["mariadb", "controller-01"],
  ["ovs-br-ex", "compute-02"],
  ["controller-01", "compute-01"],
  ["controller-01", "compute-02"],
  ["compute-01", "storage-01"],
  ["prometheus", "controller-01"],
  ["prometheus", "compute-02"],
  ["loki", "compute-01"],
  ["node-exporter", "storage-01"],
  ["qdrant", "prometheus"],
  ["grafana", "prometheus"],
];

const KIND_COLOR: Record<Vertex["kind"], string> = {
  node: "hsl(var(--info))",
  service: "hsl(var(--violet))",
  observer: "hsl(var(--success))",
};

const RAD = Math.PI / 180;

type Projected = {
  v: Vertex;
  x: number;
  y: number;
  z: number;
  scale: number;
};

function project(v: Vertex, rot: number): Projected {
  const phi = v.lat * RAD;
  const lambda = (v.lon + rot) * RAD;
  const cosPhi = Math.cos(phi);

  const x3 = R * cosPhi * Math.sin(lambda);
  const y3 = -R * Math.sin(phi);
  const z3 = R * cosPhi * Math.cos(lambda);

  const persp = FOV / (FOV - z3);
  return {
    v,
    x: CX + x3 * persp * 0.92,
    y: CY + y3 * persp * 0.92,
    z: z3,
    scale: persp,
  };
}

/** Depth cue: front-of-sphere vertices are bright, back ones fade out. */
function depthOpacity(z: number) {
  return 0.1 + 0.9 * Math.max(0, (z + R * 0.35) / (R * 1.35));
}

export function TopologyGlobe({ className = "" }: { className?: string }) {
  const reduce = useReducedMotion();
  const [rot, setRot] = useState(18);
  const last = useRef(0);

  // Capped at ~30fps: plenty smooth for a slow orbit, and keeps the SVG cheap.
  useAnimationFrame((t) => {
    if (reduce) return;
    if (t - last.current < 33) return;
    last.current = t;
    setRot((r) => (r + 0.22) % 360);
  });

  const points = VERTICES.map((v) => project(v, rot));
  const byId = new Map(points.map((p) => [p.v.id, p]));

  // Meridians squash toward a line as they turn edge-on; latitude rings never move.
  const meridians = [0, 30, 60, 90, 120, 150].map((lon) => {
    const a = (lon + rot) * RAD;
    return { lon, rx: Math.abs(Math.cos(a)) * R, front: Math.cos(a) >= 0 };
  });
  const latitudes = [-60, -30, 0, 30, 60].map((lat) => ({
    lat,
    rx: Math.cos(lat * RAD) * R,
    cy: CY - Math.sin(lat * RAD) * R,
  }));

  return (
    <div className={`relative ${className}`}>
      {/* Ambient glow behind the sphere */}
      <div
        aria-hidden="true"
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(circle at 50% 50%, hsl(var(--info) / 0.18), transparent 62%), radial-gradient(circle at 62% 68%, hsl(var(--primary) / 0.16), transparent 55%)",
        }}
      />
      <svg
        viewBox="0 0 400 400"
        className="relative w-full h-full overflow-visible"
        role="img"
        aria-label="Rotating 3D globe plotting the Cortex topology graph: OpenStack controller, compute and storage nodes wired to the services they run and the observability plane watching them."
      >
        <defs>
          <radialGradient id="globe-body" cx="38%" cy="30%" r="78%">
            <stop offset="0%" stopColor="hsl(var(--foreground))" stopOpacity="0.07" />
            <stop offset="65%" stopColor="hsl(var(--foreground))" stopOpacity="0.03" />
            <stop offset="100%" stopColor="hsl(var(--info))" stopOpacity="0.1" />
          </radialGradient>
          <radialGradient id="globe-rim" cx="50%" cy="50%" r="50%">
            <stop offset="82%" stopColor="hsl(var(--info))" stopOpacity="0" />
            <stop offset="100%" stopColor="hsl(var(--info))" stopOpacity="0.42" />
          </radialGradient>
        </defs>

        {/* Sphere body + rim light */}
        <circle cx={CX} cy={CY} r={R} fill="url(#globe-body)" />
        <circle cx={CX} cy={CY} r={R} fill="url(#globe-rim)" />
        <circle
          cx={CX}
          cy={CY}
          r={R}
          fill="none"
          stroke="hsl(var(--foreground))"
          strokeOpacity="0.14"
        />

        {/* Wireframe cage */}
        <g fill="none" stroke="hsl(var(--foreground))" strokeWidth="0.6">
          {latitudes.map((l) => (
            <ellipse
              key={`lat-${l.lat}`}
              cx={CX}
              cy={l.cy}
              rx={l.rx}
              ry={l.rx * 0.14}
              strokeOpacity={l.lat === 0 ? 0.16 : 0.09}
            />
          ))}
          {meridians.map((m) => (
            <ellipse
              key={`lon-${m.lon}`}
              cx={CX}
              cy={CY}
              rx={m.rx}
              ry={R}
              strokeOpacity={m.front ? 0.13 : 0.06}
            />
          ))}
        </g>

        {/* Topology edges — drawn as lifted arcs, like the globe's connection lines */}
        <g fill="none" strokeLinecap="round">
          {EDGES.map(([a, b]) => {
            const p1 = byId.get(a);
            const p2 = byId.get(b);
            if (!p1 || !p2) return null;
            // Skip edges whose endpoints are on the far side — keeps the cage readable.
            if (p1.z < -R * 0.2 || p2.z < -R * 0.2) return null;

            const mx = (p1.x + p2.x) / 2;
            const my = (p1.y + p2.y) / 2;
            // Push the control point away from the centre so the arc lifts off the surface.
            const ctrlX = CX + (mx - CX) * 1.34;
            const ctrlY = CY + (my - CY) * 1.34;

            const hot = p1.v.alert && p2.v.alert;
            const opacity = Math.min(depthOpacity(p1.z), depthOpacity(p2.z));

            return (
              <path
                key={`${a}-${b}`}
                d={`M ${p1.x} ${p1.y} Q ${ctrlX} ${ctrlY} ${p2.x} ${p2.y}`}
                stroke={hot ? "hsl(var(--primary))" : "hsl(var(--info))"}
                strokeWidth={hot ? 1.5 : 1}
                strokeOpacity={opacity * (hot ? 0.95 : 0.5)}
                strokeDasharray="4 7"
                className={reduce ? undefined : "arc-flow"}
                style={{ animationDelay: `${(a.length * 137) % 2600}ms` }}
              />
            );
          })}
        </g>

        {/* Vertices — painter-ordered so front markers land on top */}
        <g>
          {[...points]
            .sort((p, q) => p.z - q.z)
            .map((p) => {
              const o = depthOpacity(p.z);
              const r = (p.v.kind === "node" ? 4.1 : 2.7) * p.scale * 0.92;
              const color = p.v.alert ? "hsl(var(--primary))" : KIND_COLOR[p.v.kind];
              return (
                <g key={p.v.id}>
                  {p.v.alert && p.z > 0 && (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={r * 2.6}
                      fill={color}
                      opacity={o * 0.18}
                      className={reduce ? undefined : "animate-pulse"}
                    />
                  )}
                  <circle cx={p.x} cy={p.y} r={r} fill={color} opacity={o} />
                  {p.v.kind === "node" && (
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={r + 2.4}
                      fill="none"
                      stroke={color}
                      strokeOpacity={o * 0.45}
                      strokeWidth="0.8"
                    />
                  )}
                  {/* Only the four physical nodes get labels, and only up front */}
                  {p.v.kind === "node" && p.z > R * 0.15 && (
                    <text
                      x={p.x + r + 5}
                      y={p.y + 3}
                      fill="hsl(var(--foreground))"
                      opacity={o * 0.72}
                      style={{
                        fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
                        fontSize: 8.5,
                        fontWeight: 600,
                      }}
                    >
                      {p.v.id}
                    </text>
                  )}
                </g>
              );
            })}
        </g>
      </svg>
    </div>
  );
}
