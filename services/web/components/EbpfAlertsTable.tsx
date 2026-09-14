"use client";

import { Cpu } from "lucide-react";
import type { EbpfAlertRow } from "@/lib/securityStatus";
import { ebpfPriorityTone } from "@/lib/securityStatus";

function formatTime(iso: string) {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString() : d.toLocaleString();
}

/** Phase Sec-4: Notion-style dense table for /security/kernel-signals'
 * main view -- same sticky-header/hairline-divider shape
 * CveFindingsTable.tsx already established, one row per (host, currently-
 * active eBPF alert) rather than per host, straight off
 * AgentEbpfSignal.alerts (services/ebpf_signal.py's `get_node_ebpf_alerts`),
 * no new backend transformation needed. `output` is rendered the same
 * monospace, unwrapped-but-wrapping way AuthActivityTable.tsx renders a
 * raw Loki line, since it's the same kind of thing: a real sensor's own
 * free-text finding, not a field this dashboard summarizes further.
 */
export default function EbpfAlertsTable({ rows }: { rows: EbpfAlertRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="panel flex flex-col items-center gap-2 p-10 text-center">
        <Cpu className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
        <div className="text-sm text-text-faint">No active kernel-level alerts on any monitored host right now.</div>
      </div>
    );
  }

  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[100px_1.3fr_1fr_120px_2.4fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Priority</div>
        <div>Rule</div>
        <div>Host</div>
        <div>Time</div>
        <div>Output</div>
      </div>

      <div>
        {rows.map((row, i) => {
          const tone = ebpfPriorityTone(row.priority);
          return (
            <div
              key={`${row.hostname}:${row.rule}:${row.time}:${i}`}
              className="grid w-full gap-4 border-b p-5 text-left last:border-b-0 sm:grid-cols-[100px_1.3fr_1fr_120px_2.4fr]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <div className="flex items-center">
                <span
                  className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
                  style={{ color: tone.color, background: tone.soft }}
                >
                  {tone.label}
                </span>
              </div>

              <div className="flex min-w-0 items-center gap-2.5">
                <span
                  className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                  style={{ background: "var(--canvas)" }}
                >
                  <Cpu className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
                </span>
                <div className="min-w-0 truncate font-medium text-color-text">{row.rule}</div>
              </div>

              <div className="hidden items-center sm:flex">
                <div className="min-w-0">
                  <div className="truncate text-sm text-text-dim">{row.hostname}</div>
                  <div className="truncate text-xs text-text-faint">{row.role}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <span className="whitespace-nowrap text-xs text-text-faint">{formatTime(row.time)}</span>
              </div>

              <div className="hidden items-center sm:flex">
                <p
                  className="line-clamp-2 whitespace-pre-wrap break-all text-[12.5px] leading-relaxed text-text-faint"
                  style={{ fontFamily: "var(--font-mono)" }}
                >
                  {row.output}
                </p>
              </div>

              {/* Compact stacked view under sm -- the grid above hides
                  everything but Priority/Rule on narrow screens. */}
              <div className="col-span-1 space-y-1 text-xs text-text-faint sm:hidden">
                <div>
                  {row.hostname} · {row.role} · {formatTime(row.time)}
                </div>
                <p className="whitespace-pre-wrap break-all leading-relaxed" style={{ fontFamily: "var(--font-mono)" }}>
                  {row.output}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
