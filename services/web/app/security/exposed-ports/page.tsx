"use client";

import { Globe } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function ExposedPortsPage() {
  return (
    <ComingSoon
      icon={Globe}
      title="Exposed ports"
      scope="node"
      blockedOn="Phase Sec-5a (a host's own listening ports, cross-checked against its declared posture) hasn't shipped -- there's no backend check for this yet. A separate, not-yet-committed instance-scoped version (whether a VM itself is reachable) is tracked as Phase Sec-5b"
    />
  );
}
