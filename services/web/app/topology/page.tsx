import { Suspense } from "react";
import TopologyView from "@/components/TopologyView";

export default function TopologyPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm text-text-faint">Loading…</p>}>
      <TopologyView />
    </Suspense>
  );
}
