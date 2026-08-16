import Link from "next/link";
import { ArrowUpRight, ShieldCheck, TrendingUp, Cpu, MemoryStick, HardDrive, Activity } from "lucide-react";
import type { ThresholdWarning } from "@/lib/types";
import { thresholdEtaLabel, metricLabel } from "@/lib/thresholds";

function MetricGlyph({ metric, className }: { metric: string; className?: string }) {
  switch (metric) {
    case "cpu_percent":
      return <Cpu className={className} strokeWidth={1.75} />;
    case "memory_percent":
      return <MemoryStick className={className} strokeWidth={1.75} />;
    case "disk_percent":
      return <HardDrive className={className} strokeWidth={1.75} />;
    default:
      return <Activity className={className} strokeWidth={1.75} />;
  }
}

/** "X will hit threshold in ~N days" -- N rounded to whole days for
 * readability, with "already over" / "today" / "tomorrow" spelled out
 * for the near-term edge cases rather than showing "~0 days". */

export default function ThresholdWarningsPanel({ warnings }: { warnings: ThresholdWarning[] }) {
  return (
    <div className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b p-4" style={{ borderColor: "var(--border-soft)" }}>
        <div>
          <div className="eyebrow">Forecasting</div>
          <h3 className="mt-1 text-[15px] font-semibold text-color-text">Threshold warnings</h3>
        </div>
        <Link href="/forecast" className="inline-flex items-center gap-1 text-xs font-medium text-text-faint transition-colors hover:text-color-text">
          Open forecast
          <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
        </Link>
      </div>

      {warnings.length === 0 ? (
        <div className="flex flex-col items-center gap-2 p-8 text-center">
          <ShieldCheck className="h-5 w-5" style={{ color: "var(--ok)" }} strokeWidth={1.75} />
          <p className="text-sm text-text-faint">No resource is projected to hit its threshold in the next 7 days.</p>
        </div>
      ) : (
        <div className="max-h-[360px] divide-y overflow-y-auto" style={{ borderColor: "var(--border-soft)" }}>
          {warnings.map((warning) => {
            const critical = warning.already_breached;
            const color = critical ? "var(--crit)" : "var(--warn)";
            const soft = critical ? "var(--crit-soft)" : "var(--warn-soft)";
            return (
              <Link
                key={`${warning.hostname}-${warning.metric}`}
                href={`/forecast?host=${encodeURIComponent(warning.hostname)}&metric=${encodeURIComponent(warning.metric)}`}
                className="flex items-start gap-3 p-3.5 transition-colors hover:bg-[var(--canvas)]"
                style={{ borderColor: "var(--border-soft)" }}
              >
                <span
                  className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full"
                  style={{ background: soft, color }}
                >
                  <MetricGlyph metric={warning.metric} className="h-3.5 w-3.5" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className="rounded px-1.5 py-0.5 font-semibold" style={{ color, background: soft }}>
                      {critical ? "CRITICAL" : "WARNING"}
                    </span>
                    <span className="truncate text-text-dim">{warning.hostname}</span>
                  </div>
                  <div className="mt-1 text-[13px] text-color-text">
                    <span className="font-medium">{warning.hostname}</span>
                    {" "}
                    {metricLabel(warning.metric).toLowerCase()} will hit {warning.threshold}%{" "}
                    <span className="font-semibold" style={{ color }}>
                      {thresholdEtaLabel(warning)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-1.5 text-[11px] text-text-faint">
                    <TrendingUp className="h-3 w-3" strokeWidth={2} />
                    now at {warning.current_value}%
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
