"use client";

import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { Card } from "@/components/ui/Card";
import type { AuthInsight } from "@/lib/securityStatus";

const TONE_META = {
  critical: { Icon: ShieldAlert, color: "var(--crit)", soft: "var(--crit-soft)", label: "Active signal" },
  warning: { Icon: ShieldQuestion, color: "var(--warn)", soft: "var(--warn-soft)", label: "Partially checked" },
  clean: { Icon: ShieldCheck, color: "var(--ok)", soft: "var(--ok-soft)", label: "Clean" },
} as const;

/** Phase Sec-6 add-on: the "what's actually going on" summary at the top
 * of /security/auth-activity, built from buildAuthInsight -- one glance at
 * which host(s) are affected, what kind of activity it is, how much, and
 * how fresh, before the per-line table below. Every fact here traces back
 * to a field the page was already fetching (detail/entries), nothing new
 * is invented to fill the card out.
 */
export default function AuthActivityInsight({ insight }: { insight: AuthInsight }) {
  const meta = TONE_META[insight.tone];
  const { Icon } = meta;

  return (
    <Card padding="p-5">
      <div className="flex items-start gap-3">
        <div
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
          style={{ background: meta.soft, color: meta.color }}
        >
          <Icon className="h-4 w-4" strokeWidth={2} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.06em]"
              style={{ background: meta.soft, color: meta.color }}
            >
              {meta.label}
            </span>
            {insight.categoryBreakdown.length > 0 && (
              <span className="text-[11px] text-text-faint">
                Dominant pattern:{" "}
                <span className="font-medium text-text-dim">{insight.categoryBreakdown[0].label}</span>
                {insight.categoryBreakdown.length > 1 && (
                  <span> ({insight.categoryBreakdown[0].count} of {insight.totalFlaggedEntries || insight.categoryBreakdown.reduce((s, c) => s + c.count, 0)} matches)</span>
                )}
              </span>
            )}
          </div>

          <p className="mt-1.5 text-[13.5px] leading-relaxed text-color-text">{insight.headline}</p>

          {insight.bullets.length > 0 && (
            <ul className="mt-3 space-y-1.5 border-t pt-3 text-[12.5px] text-text-dim" style={{ borderColor: "var(--border-soft)" }}>
              {insight.bullets.map((bullet, i) => (
                <li key={i} className="flex items-start gap-2">
                  <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full" style={{ background: meta.color }} />
                  <span>{bullet}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}
