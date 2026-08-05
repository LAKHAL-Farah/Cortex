import { Suspense } from "react";
import ForecastExplorer from "@/components/ForecastExplorer";

export default function ForecastPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm text-text-faint">Loading…</p>}>
      <ForecastExplorer />
    </Suspense>
  );
}
