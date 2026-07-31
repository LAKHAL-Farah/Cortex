import Link from "next/link";
import { ArrowUpRight, ShieldCheck } from "lucide-react";
import type { LogEntry, LogLevel } from "@/lib/types";
import { LEVEL_COLOR, LEVEL_SOFT, formatRelativeTime } from "@/lib/logs";

export type Issue = LogEntry & { level: LogLevel };

export default function RecentIssuesPanel({ issues, totalCount }: { issues: Issue[]; totalCount: number }) {
  return (
    <div className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b p-4" style={{ borderColor: "var(--border-soft)" }}>
        <div>
          <div className="eyebrow">Monitoring</div>
          <h3 className="mt-1 text-[15px] font-semibold text-color-text">Recent issues</h3>
        </div>
        <Link href="/logs" className="inline-flex items-center gap-1 text-xs font-medium text-text-faint transition-colors hover:text-color-text">
          View all
          <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
        </Link>
      </div>

      {issues.length === 0 ? (
        <div className="flex flex-col items-center gap-2 p-8 text-center">
          <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
          <p className="text-sm text-text-faint">No warnings or errors in the last hour.</p>
        </div>
      ) : (
        <div className="max-h-[360px] divide-y overflow-y-auto" style={{ borderColor: "var(--border-soft)" }}>
          {issues.map((issue, i) => (
            <Link
              key={`${issue.ts}-${issue.host}-${i}`}
              href="/logs"
              className="flex items-start gap-3 p-3.5 transition-colors hover:bg-[var(--canvas)]"
              style={{ borderColor: "var(--border-soft)" }}
            >
              <span className="mt-1.5 h-2 w-2 flex-shrink-0 rounded-full" style={{ background: LEVEL_COLOR[issue.level] }} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[11px]">
                  <span
                    className="rounded px-1.5 py-0.5 font-semibold"
                    style={{ color: LEVEL_COLOR[issue.level], background: LEVEL_SOFT[issue.level] }}
                  >
                    {issue.level}
                  </span>
                  <span className="truncate text-text-dim">{issue.host}</span>
                  {issue.source && <span className="truncate text-text-faint">· {issue.source}</span>}
                  <span className="ml-auto flex-shrink-0 text-text-faint">{formatRelativeTime(issue.ts)}</span>
                </div>
                <div className="mt-1 truncate text-[12.5px] text-color-text" style={{ fontFamily: "var(--font-mono)" }}>
                  {issue.line}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      {totalCount > issues.length && (
        <div className="border-t p-3 text-center text-xs text-text-faint" style={{ borderColor: "var(--border-soft)" }}>
          +{totalCount - issues.length} more in the last hour
        </div>
      )}
    </div>
  );
}
