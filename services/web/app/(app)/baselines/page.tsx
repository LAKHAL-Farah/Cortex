import { Suspense } from "react";
import BaselineExplorer from "@/components/BaselineExplorer";

export default function BaselinesPage() {
  return (
    <Suspense fallback={<p className="p-6 text-sm text-text-faint">Loading…</p>}>
      <BaselineExplorer />
    </Suspense>
  );
}
