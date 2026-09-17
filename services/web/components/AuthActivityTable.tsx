"use client";

import Link from "next/link";
import { ExternalLink, ShieldAlert, Terminal } from "lucide-react";
import type { AuthLogRow } from "@/lib/securityStatus";

function formatTime(ts: number) {
  const d = new Date(ts);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString() : d.toLocaleString();
}

/** Phase Sec-6: Notion-style dense table for /security/auth-activity's
 * main view -- same sticky-header/hairline-divider shape
 * CveFindingsTable.tsx already established, one row per (host, correlated
 * auth-failure log line) rather than per host, straight off
 * AgentAuthAnomalySignal.entries, no new backend transformation needed.
 * The raw log line itself is rendered the same way LogViewer.tsx's
 * LogRow already does (monospace, wrapped, not truncated to a single
 * line) since it's the same kind of raw Loki line, just pre-filtered to
 * auth-failure matches instead of every log level. Each row links out to
 * the full Logs page for that host the same way CopilotAgentPanels'
 * "Check all logs" link already does, rather than re-building log
 * search/filtering here.
 */
export default function AuthActivityTable({ rows }: { rows: AuthLogRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="panel flex flex-col items-center gap-2 p-10 text-center">
        <ShieldAlert className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
        <div className="text-sm text-text-faint">No correlated auth-failure log entries on any monitored host right now.</div>
      </div>
    );
  }

  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[104px_1fr_110px_2.4fr_28px] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Time</div>
        <div>Host</div>
        <div>Service</div>
        <div>Log line</div>
        <div />
      </div>

      <div>
        {rows.map((row, i) => (
          <div
            key={`${row.hostname}:${row.ts}:${i}`}
            className="grid w-full items-start gap-4 border-b p-5 text-left last:border-b-0 sm:grid-cols-[104px_1fr_110px_2.4fr_28px]"
            style={{ borderColor: "var(--border-soft)" }}
          >
            <div className="stat-figure whitespace-nowrap text-[11px] text-text-faint">{formatTime(row.ts)}</div>

            <div className="hidden min-w-0 sm:block">
              <div className="truncate font-medium text-color-text">{row.hostname}</div>
              <div className="mt-0.5 truncate text-xs text-text-faint">{row.role}</div>
            </div>

            <div className="hidden sm:block">
              <span
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium text-text-dim"
                style={{ background: "var(--canvas)" }}
              >
                <Terminal className="h-2.5 w-2.5 shrink-0" strokeWidth={2} />
                {row.service ?? "unknown"}
              </span>
            </div>

            <div
              className="hidden whitespace-pre-wrap break-all text-[12.5px] leading-relaxed text-text-dim sm:block"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {row.line}
            </div>

            <div className="hidden sm:flex sm:items-start sm:justify-end">
              <Link
                href={`/logs?host=${encodeURIComponent(row.hostname)}&minutes=60`}
                title="Check all logs for this host"
                className="text-text-faint transition-colors hover:text-color-text"
              >
                <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} />
              </Link>
            </div>

            {/* Compact stacked view under sm -- the grid above hides everything
                but Time on narrow screens, so repeat the rest here. */}
            <div className="col-span-1 space-y-1.5 text-xs sm:hidden">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <span className="font-medium text-color-text">{row.hostname}</span>{" "}
                  <span className="text-text-faint">· {row.role} · {row.service ?? "unknown"}</span>
                </div>
                <Link href={`/logs?host=${encodeURIComponent(row.hostname)}&minutes=60`} className="shrink-0 text-text-faint">
                  <ExternalLink className="h-3.5 w-3.5" strokeWidth={2} />
                </Link>
              </div>
              <p className="whitespace-pre-wrap break-all text-[12px] leading-relaxed text-text-dim" style={{ fontFamily: "var(--font-mono)" }}>
                {row.line}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
