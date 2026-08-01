import { Suspense } from "react";
import AnomalyHistoryView from "@/components/AnomalyHistoryView";

export default function AnomalyHistoryPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm text-text-faint">Loading…</p>}>
      <AnomalyHistoryView />
    </Suspense>
  );
}
