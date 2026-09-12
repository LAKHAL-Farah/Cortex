"use client";

import { Cpu } from "lucide-react";
import ComingSoon from "@/components/ComingSoon";

export default function KernelSignalsPage() {
  return (
    <ComingSoon
      icon={Cpu}
      title="Kernel-level signals (eBPF)"
      blockedOn="Phase Sec-4 (a Falco/Tetragon-style sensor deployment) hasn't shipped, so there's no real kernel-alert feed behind this yet"
    />
  );
}
