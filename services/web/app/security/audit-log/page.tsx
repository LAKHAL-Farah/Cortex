"use client";

import { FileClock } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function AuditLogPage() {
  return (
    <ComingSoon
      icon={FileClock}
      title="Security audit log"
      blockedOn="this needs its own persisted, queryable record of who asked what and whether it was redacted -- today that logic runs per-request (services/security_rbac.py) but isn't written anywhere for later review"
    />
  );
}
