import { Suspense } from "react";
import LogViewer from "@/components/LogViewer";

export default function LogsPage() {
  // LogViewer reads ?host=/?minutes= via useSearchParams (deep link from the
  // anomaly agent panel's "Check all logs" button) -- Next's app router
  // requires that behind a Suspense boundary.
  return (
    <Suspense fallback={null}>
      <LogViewer />
    </Suspense>
  );
}
