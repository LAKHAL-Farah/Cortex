import { Suspense } from "react";
import RemediationAuditView from "@/components/RemediationAuditView";

export default function RemediationAuditPage() {
  // useSearchParams (deep-linking an incident) needs a Suspense boundary.
  return (
    <Suspense fallback={null}>
      <RemediationAuditView />
    </Suspense>
  );
}
