"use client";

import Link from "next/link";
import { ChevronRight, Shield } from "lucide-react";
import type { SecurityFinding, AgentSecuritySignal } from "@/lib/types";
import { securityPillTone, overallSecurityTone } from "@/lib/securityStatus";

function Pill({ signal }: { signal: AgentSecuritySignal }) {
  const tone = securityPillTone(signal);
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ color: tone.color, background: tone.soft }}
    >
      {tone.label}
    </span>
  );
}

/** Phase Sec-5: "Per-host posture", reworked from a grid of big tile-cards
 * into the same Notion-style dense table ServiceTable.tsx uses for
 * /services -- sticky-feeling header row, hairline row dividers, whole
 * row clickable through to the per-host security-groups detail page. The
 * five sub-check pills collapse on narrow viewports the same way
 * ServiceTable's secondary columns do, leaving host + overall status. */
export default function SecurityPostureTable({ findings }: { findings: SecurityFinding[] }) {
  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[1.8fr_0.9fr_0.9fr_0.9fr_0.9fr_0.9fr_1fr] gap-3 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Host</div>
        <div>Auth activity</div>
        <div>Security groups</div>
        <div>Known CVEs</div>
        <div>Exposed ports</div>
        <div>Kernel signals</div>
        <div className="text-right">Overall</div>
      </div>

      <div>
        {findings.map((f) => {
          const tone = overallSecurityTone(f.raw_data);
          return (
            <Link
              key={f.hostname}
              href={`/security/security-groups/${encodeURIComponent(f.hostname)}`}
              className="group grid w-full gap-3 border-b p-5 text-left transition-colors last:border-b-0 hover:bg-[var(--canvas)] sm:grid-cols-[1.8fr_0.9fr_0.9fr_0.9fr_0.9fr_0.9fr_1fr]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <div className="flex min-w-0 items-center gap-3">
                <span
                  className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                  style={{ background: `color-mix(in srgb, ${tone.color} 14%, transparent)` }}
                >
                  <Shield className="h-4 w-4" style={{ color: tone.color }} strokeWidth={1.75} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-medium text-color-text">{f.hostname}</div>
                  <div className="mt-0.5 truncate text-sm text-text-faint">{f.role}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <Pill signal={f.raw_data.auth_signal} />
              </div>
              <div className="hidden items-center sm:flex">
                <Pill signal={f.raw_data.sec_group_signal} />
              </div>
              <div className="hidden items-center sm:flex">
                <Pill signal={f.raw_data.cve_signal} />
              </div>
              <div className="hidden items-center sm:flex">
                <Pill signal={f.raw_data.exposed_port_signal} />
              </div>
              <div className="hidden items-center sm:flex">
                <Pill signal={f.raw_data.ebpf_signal} />
              </div>

              <div className="flex items-center justify-end gap-2">
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
                  style={{ color: tone.color, background: tone.soft }}
                >
                  <span className="status-dot" style={{ background: tone.color }} />
                  {tone.label}
                </span>
                <ChevronRight
                  className="h-4 w-4 shrink-0 text-text-faint transition-transform group-hover:translate-x-0.5"
                  strokeWidth={2}
                />
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
