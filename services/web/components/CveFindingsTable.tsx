"use client";

import { Package, ShieldAlert } from "lucide-react";
import type { CveFindingRow } from "@/lib/securityStatus";
import { cveSeverityTone } from "@/lib/securityStatus";

/** Phase Sec-3: Notion-style dense table for /security/vulnerabilities'
 * main view -- same sticky-header/hairline-divider shape
 * SecurityGroupsTable.tsx already established, one row per (host,
 * matched CVE) rather than per host, since that's the actual unit the
 * roadmap doc's own page spec asks for: "package, installed version,
 * matched CVE ID, severity, fixed version, one-line description --
 * straight off match_cves's existing return shape, no new backend
 * transformation needed." No per-row link: there's no per-host CVE
 * detail page (unlike security-groups' drill-down), so this table is the
 * full picture on its own.
 */
export default function CveFindingsTable({ rows }: { rows: CveFindingRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="panel flex flex-col items-center gap-2 p-10 text-center">
        <ShieldAlert className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
        <div className="text-sm text-text-faint">No known-vulnerable package versions matched on any monitored host.</div>
      </div>
    );
  }

  return (
    <div className="panel overflow-hidden">
      <div
        className="hidden grid-cols-[1fr_1.3fr_0.9fr_1fr_1fr_2fr] gap-4 px-5 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
        style={{ background: "var(--canvas)" }}
      >
        <div>Severity</div>
        <div>Package</div>
        <div>Host</div>
        <div>CVE</div>
        <div>Fixed in</div>
        <div>Description</div>
      </div>

      <div>
        {rows.map((row) => {
          const tone = cveSeverityTone(row.severity);
          return (
            <div
              key={`${row.hostname}:${row.package}:${row.cve_id}`}
              className="grid w-full gap-4 border-b p-5 text-left last:border-b-0 sm:grid-cols-[1fr_1.3fr_0.9fr_1fr_1fr_2fr]"
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
                  <Package className="h-4 w-4 text-text-dim" strokeWidth={1.75} />
                </span>
                <div className="min-w-0">
                  <div className="truncate font-medium text-color-text">{row.package}</div>
                  <div className="mt-0.5 truncate text-xs text-text-faint">installed {row.installed_version}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <div className="min-w-0">
                  <div className="truncate text-sm text-text-dim">{row.hostname}</div>
                  <div className="truncate text-xs text-text-faint">{row.role}</div>
                </div>
              </div>

              <div className="hidden items-center sm:flex">
                <span className="truncate font-mono text-xs text-text-dim">{row.cve_id}</span>
              </div>

              <div className="hidden items-center sm:flex">
                <span className="text-sm text-text-dim">{row.fixed_version}</span>
              </div>

              <div className="hidden items-center sm:flex">
                <p className="line-clamp-2 text-sm text-text-faint">{row.description}</p>
              </div>

              {/* Compact stacked view under sm -- the grid above hides everything
                  but Severity/Package on narrow screens, so repeat the rest here. */}
              <div className="col-span-1 space-y-1 text-xs text-text-faint sm:hidden">
                <div>
                  {row.hostname} · {row.role}
                </div>
                <div className="font-mono">
                  {row.cve_id} · fixed in {row.fixed_version}
                </div>
                <p className="line-clamp-2">{row.description}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
