"use client";

import { ScrollText } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function VulnerabilitiesPage() {
  return (
    <ComingSoon
      icon={ScrollText}
      title="Vulnerabilities (CVE)"
      scope="node"
      blockedOn="Phase Sec-3 (the package-inventory collector) hasn't shipped, so there's no real per-host package data behind this yet"
    />
  );
}
