"use client";

import { KeyRound } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function KeystoneTokensPage() {
  return (
    <ComingSoon
      icon={KeyRound}
      title="Keystone tokens"
      blockedOn="Phase Sec-5 hasn't shipped -- there's no backend check for this yet"
    />
  );
}
