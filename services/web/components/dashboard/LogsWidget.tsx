"use client";

import React from "react";
import Link from "next/link";
import { ShieldCheck } from "lucide-react";
import Widget, { Skeleton, Unavailable } from "./Widget";
import { useLogs } from "@/lib/useDashboardData";
import { LEVEL_COLOR, LEVEL_SOFT, formatRelativeTime } from "@/lib/logs";

const BUCKETS = 12; // 12 x 5 min = last hour

/** Last-hour log volume (stacked by level) plus the newest warnings/errors. */
export default function LogsWidget() {
  const { parsed, error, isLoading } = useLogs();
  // Ticks with the 5s poll so the 60-minute window slides forward.
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);
  const bucketMs = 5 * 60_000;
  const start = now - BUCKETS * bucketMs;

  const buckets = Array.from({ length: BUCKETS }, () => ({ ERROR: 0, WARNING: 0, other: 0 }));
  for (const l of parsed) {
    const idx = Math.floor((l.ts - start) / bucketMs);
    if (idx < 0 || idx >= BUCKETS) continue;
    if (l.level === "ERROR") buckets[idx].ERROR++;
    else if (l.level === "WARNING") buckets[idx].WARNING++;
    else buckets[idx].other++;
  }
  const maxTotal = Math.max(1, ...buckets.map((b) => b.ERROR + b.WARNING + b.other));
  const counts = {
    ERROR: parsed.filter((l) => l.level === "ERROR").length,
    WARNING: parsed.filter((l) => l.level === "WARNING").length,
    total: parsed.length,
  };
  const issues = parsed.filter((l) => l.level === "ERROR" || l.level === "WARNING").slice(0, 4);

  return (
    <Widget eyebrow="Logs" title="Log activity · last hour" href="/logs" hrefLabel="Open logs" fill>
      {isLoading ? (
        <Skeleton h={200} />
      ) : error ? (
        <Unavailable />
      ) : (
        <div className="grid flex-1 gap-4 md:grid-cols-[1fr_1.1fr]">
          <div className="flex min-w-0 flex-col">
            <div className="mb-3 flex gap-2">
              {[
                { k: "Errors", v: counts.ERROR, c: LEVEL_COLOR.ERROR, s: LEVEL_SOFT.ERROR },
                { k: "Warnings", v: counts.WARNING, c: LEVEL_COLOR.WARNING, s: LEVEL_SOFT.WARNING },
                { k: "Lines", v: counts.total, c: "var(--text)", s: "var(--canvas)" },
              ].map((x) => (
                <div key={x.k} className="flex-1 rounded-[var(--radius-control)] px-2.5 py-1.5" style={{ background: x.s }}>
                  <div className="stat-figure text-base" style={{ color: x.c }}>{x.v}</div>
                  <div className="text-[10px] text-text-faint">{x.k}</div>
                </div>
              ))}
            </div>
            <div className="flex min-h-[96px] flex-1 items-end gap-1" role="img" aria-label="Log volume per 5 minutes, last hour">
              {buckets.map((b, i) => {
                const total = b.ERROR + b.WARNING + b.other;
                const h = (total / maxTotal) * 100;
                return (
                  <div key={i} className="flex h-full flex-1 flex-col justify-end" title={`${b.ERROR} errors · ${b.WARNING} warnings · ${b.other} other`}>
                    <div className="flex w-full flex-col justify-end overflow-hidden rounded-[2px]" style={{ height: `${h}%`, minHeight: 3, background: total ? undefined : "color-mix(in srgb, var(--text-muted) 22%, transparent)" }}>
                      <div style={{ flex: b.ERROR, background: LEVEL_COLOR.ERROR }} />
                      <div style={{ flex: b.WARNING, background: LEVEL_COLOR.WARNING }} />
                      <div style={{ flex: b.other, background: "color-mix(in srgb, var(--neutral) 55%, transparent)" }} />
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-1 flex justify-between text-[10px] text-text-muted"><span>60m ago</span><span>now</span></div>
          </div>

          <div className="min-w-0">
            {issues.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 py-6 text-center">
                <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
                <p className="text-xs text-text-faint">No warnings or errors in the last hour.</p>
              </div>
            ) : (
              <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
                {issues.map((l, i) => (
                  <Link key={`${l.ts}-${i}`} href={l.host ? `/logs?host=${encodeURIComponent(l.host)}` : "/logs"} className="block py-2 first:pt-0 hover:opacity-80">
                    <div className="flex items-center gap-2 text-[11px] text-text-faint">
                      <span className="rounded px-1.5 py-px text-[10px] font-semibold" style={{ color: LEVEL_COLOR[l.level!], background: LEVEL_SOFT[l.level!] }}>{l.level}</span>
                      <span className="truncate">{l.host ?? "—"}</span>
                      <span className="ml-auto flex-shrink-0">{formatRelativeTime(l.ts)}</span>
                    </div>
                    <div className="mt-1 truncate text-xs text-text-dim" style={{ fontFamily: "var(--font-mono)" }}>{l.line}</div>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </Widget>
  );
}
