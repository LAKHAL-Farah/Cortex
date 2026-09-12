"use client";

import { Globe } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function ExposedPortsPage() {
  return (
    <ComingSoon
      icon={Globe}
      title="Exposed ports"
      blockedOn="Phase Sec-5 hasn't shipped -- there's no backend check for this yet"
    />
  );
}
