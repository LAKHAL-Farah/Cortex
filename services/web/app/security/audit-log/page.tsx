"use client";

import { FileClock } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function AuditLogPage() {
  return (
    <ComingSoon
      icon={FileClock}
      title="Security audit log"
      scope="identity"
      blockedOn="this needs its own persisted, queryable record of who asked what and whether it was redacted -- today that logic runs per-request (services/security_rbac.py) but isn't written anywhere for later review. Tagged Identity because the log is keyed by actor/project, even though individual entries can concern a node, an instance, or Keystone itself"
    />
  );
}
