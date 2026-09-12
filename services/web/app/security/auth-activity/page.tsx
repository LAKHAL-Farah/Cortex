"use client";

import { Terminal } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function AuthActivityPage() {
  return (
    <ComingSoon
      icon={Terminal}
      title="Auth activity"
      blockedOn="this needs its own correlated-burst timeline endpoint, not just the pass/fail summary GET /api/v1/security/findings already returns per host"
    />
  );
}
