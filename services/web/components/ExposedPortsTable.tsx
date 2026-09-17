"use client";

import { Globe, ShieldCheck } from "lucide-react";
import type { ExposedPortRow } from "@/lib/securityStatus";

/** Phase Sec-5a: Notion-style dense table for /security/exposed-ports'
 * main view -- same sticky-header/hairline-divider shape
 * CveFindingsTable.tsx already established, one row per (host, confirmed
 * exposed port) -- a security-group rule that world-opens a port range
 * AND a real process on that host is actually bound inside it. Unlike
 * CVE severity, every row here already represents the same kind of
 * finding (a confirmed, not just theoretical, exposure), so there's no
 * severity column -- see lib/securityStatus.ts's own comment on this.
 */
export default function ExposedPortsTable({ rows }: { rows: ExposedPortRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="panel flex flex-col items-center gap-2 p-10 text-center">
        <ShieldCheck className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
        <div className="text-sm text-text-faint">
          No confirmed exposed ports on any monitored host -- either nothing world-open is actually listening, or
          nothing&apos;s listening at all.
        </div>
      </div>
    );
  }

  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[0.8fr_1fr_1fr_1.2fr_2fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Port</div>
        <div>Host</div>
        <div>Process</div>
        <div>Security group</div>
        <div>Why it&apos;s flagged</div>
      </div>

      <div>
        {rows.map((row) => (
          <div
            key={`${row.hostname}:${row.port}:${row.security_group}`}
            className="grid w-full gap-4 border-b p-5 text-left last:border-b-0 sm:grid-cols-[0.8fr_1fr_1fr_1.2fr_2fr]"
            style={{ borderColor: "var(--border-soft)" }}
          >
            <div className="flex items-center gap-2.5">
              <span
                className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
                style={{ background: "var(--crit-soft)" }}
              >
                <Globe className="h-4 w-4" style={{ color: "var(--crit)" }} strokeWidth={1.75} />
              </span>
              <span className="font-mono font-medium text-color-text">{row.port}</span>
              {row.protocol && <span className="text-xs uppercase text-text-faint">{row.protocol}</span>}
            </div>

            <div className="hidden items-center sm:flex">
              <div className="min-w-0">
                <div className="truncate text-sm text-text-dim">{row.hostname}</div>
                <div className="truncate text-xs text-text-faint">{row.role}</div>
              </div>
            </div>

            <div className="hidden items-center sm:flex">
              <span className="truncate font-mono text-xs text-text-dim">{row.process ?? "unknown process"}</span>
            </div>

            <div className="hidden items-center sm:flex">
              <span className="truncate text-sm text-text-dim">{row.security_group}</span>
            </div>

            <div className="hidden items-center sm:flex">
              <p className="line-clamp-2 text-sm text-text-faint">{row.reason}</p>
            </div>

            {/* Compact stacked view under sm -- the grid above hides
                everything but Port on narrow screens, so repeat the rest
                here. */}
            <div className="col-span-1 space-y-1 text-xs text-text-faint sm:hidden">
              <div>
                {row.hostname} · {row.role}
              </div>
              <div className="font-mono">{row.process ?? "unknown process"}</div>
              <div>{row.security_group}</div>
              <p className="line-clamp-2">{row.reason}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
