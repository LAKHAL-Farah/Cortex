"use client";

import React from "react";
import useSWR from "swr";
import Widget, { Skeleton, Unavailable } from "./Widget";
import { apiFetcher, useFleet } from "@/lib/useDashboardData";
import type { BaselineSlot } from "@/lib/types";
import { BASELINE_METRICS, HOURS, WEEKDAY_LABELS, coverage, indexSlots, jsDayToWeekday, slotKey } from "@/lib/baselines";

const METRIC_SHORT: Record<string, string> = { cpu_usage: "CPU", ram_usage: "Memory" };

/** Mini 7x24 "what's normal for this node" grid -- the same learned weekly
 * baseline the Baselines page explores, with the current hour outlined and the
 * live value compared against it. */
export default function BaselineHeatmapWidget() {
  const { data: nodes } = useFleet();
  const hostnames = React.useMemo(() => (nodes ?? []).map((n) => n.hostname).sort(), [nodes]);
  const [host, setHost] = React.useState("");
  const [metric, setMetric] = React.useState<string>(BASELINE_METRICS[0]);
  const effective = host && hostnames.includes(host) ? host : hostnames[0] ?? "";

  const { data, error, isLoading } = useSWR<BaselineSlot[]>(
    effective ? `/api/baselines/${encodeURIComponent(effective)}?metric_name=${encodeURIComponent(metric)}` : null,
    apiFetcher,
    { refreshInterval: 5 * 60_000, shouldRetryOnError: false },
  );
  const slots = React.useMemo(() => (Array.isArray(data) ? data : []), [data]);
  const byKey = React.useMemo(() => indexSlots(slots), [slots]);
  const peak = slots.length ? Math.max(...slots.map((s) => s.median)) : 0;
  const scale = Math.max(peak * 1.1, 20);

  const now = new Date();
  const nowWeekday = jsDayToWeekday(now.getDay());
  const nowHour = now.getHours();
  const nowSlot = byKey.get(slotKey(nowWeekday, nowHour));
  const node = (nodes ?? []).find((n) => n.hostname === effective);
  const live = metric === "cpu_usage" ? node?.metrics?.cpu_percent : node?.metrics?.memory_percent;

  let verdict = "No baseline for this hour yet";
  let verdictColor = "var(--text-faint)";
  if (nowSlot && live != null) {
    const spread = Math.max(nowSlot.mad * 1.4826, 1);
    const z = (live - nowSlot.median) / spread;
    if (Math.abs(z) < 2) { verdict = "Within the normal range"; verdictColor = "var(--ok)"; }
    else { verdict = z > 0 ? "Running above normal" : "Running below normal"; verdictColor = Math.abs(z) >= 3 ? "var(--crit)" : "var(--warn)"; }
  }

  return (
    <Widget
      fill
      eyebrow="Baselines"
      title="Weekly baseline"
      href={effective ? `/baselines?host=${encodeURIComponent(effective)}&metric=${metric}` : "/baselines"}
      hrefLabel="Explore"
      right={
        <div className="flex items-center gap-1.5">
          <select
            value={effective}
            onChange={(e) => setHost(e.target.value)}
            aria-label="Node"
            className="max-w-[110px] rounded-[var(--radius-control)] px-2 py-1 text-xs text-color-text outline-none"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          >
            {hostnames.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
          <div className="inline-flex overflow-hidden rounded-[var(--radius-control)]" style={{ border: "1px solid var(--border)" }}>
            {BASELINE_METRICS.map((m) => (
              <button
                key={m}
                onClick={() => setMetric(m)}
                className="px-2 py-1 text-xs font-medium"
                style={{ background: metric === m ? "var(--accent-soft)" : "transparent", color: metric === m ? "var(--accent)" : "var(--text-faint)" }}
              >
                {METRIC_SHORT[m]}
              </button>
            ))}
          </div>
        </div>
      }
    >
      {isLoading || !effective ? (
        <Skeleton h={190} />
      ) : error || slots.length === 0 ? (
        <Unavailable text="Baseline still learning — check back after a few days of data." />
      ) : (
        <>
          <div className="grid gap-[3px]" style={{ gridTemplateColumns: "20px repeat(24, minmax(0, 1fr))" }}>
            {WEEKDAY_LABELS.map((d, wd) => (
              <React.Fragment key={d}>
                <div className="flex items-center text-[9px] text-text-muted">{d.slice(0, 2)}</div>
                {HOURS.map((h) => {
                  const s = byKey.get(slotKey(wd, h));
                  const isNow = wd === nowWeekday && h === nowHour;
                  const pct = s ? Math.round(Math.min(s.median / scale, 1) * 85) + 8 : 0;
                  return (
                    <div
                      key={h}
                      title={s ? `${d} ${String(h).padStart(2, "0")}:00 — median ${s.median.toFixed(1)}%` : `${d} ${h}:00 — no data`}
                      className="aspect-square rounded-[2px]"
                      style={{
                        background: s ? `color-mix(in srgb, var(--accent) ${pct}%, var(--canvas))` : "var(--canvas)",
                        outline: isNow ? "2px solid var(--text)" : "none",
                        outlineOffset: isNow ? 1 : 0,
                      }}
                    />
                  );
                })}
              </React.Fragment>
            ))}
          </div>
          <div className="mb-3 mt-1.5 flex justify-between pl-[23px] pr-0 text-[9px] text-text-muted">
            <span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>11p</span>
          </div>
          <div className="mt-auto flex items-center justify-between gap-3 rounded-[var(--radius-control)] px-3 py-2" style={{ background: "var(--canvas)" }}>
            <div className="min-w-0">
              <div className="text-xs font-medium" style={{ color: verdictColor }}>{verdict}</div>
              <div className="text-[11px] text-text-faint">
                {live != null ? `Now ${live.toFixed(1)}%` : "Now —"}
                {nowSlot ? ` · usual ${nowSlot.median.toFixed(1)}% for this hour` : ""}
              </div>
            </div>
            <div className="flex-shrink-0 text-right text-[11px] text-text-faint">{coverage(slots)}% learned</div>
          </div>
        </>
      )}
    </Widget>
  );
}
