import Link from "next/link";
import { ArrowLeft, Waypoints } from "lucide-react";
import NetworkTopologyCanvas from "@/components/NetworkTopologyCanvas";

export default function NetworkTopologyMapPage() {
  return (
    <main className="grid gap-4">
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-start gap-3">
          <span
            className="mt-0.5 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
            style={{ background: "var(--accent-soft)" }}
          >
            <Waypoints className="h-4.5 w-4.5" style={{ color: "var(--accent)" }} strokeWidth={2} />
          </span>
          <div>
            <div className="eyebrow">Infrastructure</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Network topology map</h1>
            <p className="mt-1 text-sm text-text-faint">
              A Horizon-style view of every network at once, provider and self-service side by side.
            </p>
          </div>
        </div>
        <Link
          href="/networks"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium text-text-dim transition-colors hover:bg-[var(--canvas)] hover:text-color-text"
          style={{ border: "1px solid var(--border)" }}
        >
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
          Back to networks
        </Link>
      </div>

      <NetworkTopologyCanvas />
    </main>
  );
}
