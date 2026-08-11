"use client";

import React from "react";
import { Card } from "./Card";
import SparklineChart from "./SparklineChart";
import { ArrowUpRight, ArrowDownRight, type LucideIcon } from "lucide-react";

export default function MetricCard({
  title,
  value,
  unit,
  trend,
  trendLabel,
  sparklineData,
  sparklineColor,
  icon: Icon,
  iconColor,
}: {
  title: string;
  value: string | number;
  unit?: string;
  trend?: number;
  trendLabel?: string;
  sparklineData?: { name: string; value: number }[];
  sparklineColor?: string;
  /** Small square "asset" badge in the header, same visual pattern as the
   * per-type icon badges on the Services/Networks list rows -- optional so
   * existing callers keep working without one. */
  icon?: LucideIcon;
  iconColor?: string;
}) {
  const trendPositive = (trend ?? 0) >= 0;
  const badgeColor = iconColor ?? sparklineColor ?? "var(--accent)";
  return (
    <Card interactive className="flex flex-col justify-between gap-3">
      <div>
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-medium text-text-dim">{title}</div>
          {Icon ? (
            <span
              className="grid h-8 w-8 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
              style={{ background: `color-mix(in srgb, ${badgeColor} 14%, transparent)` }}
            >
              <Icon className="h-4 w-4" style={{ color: badgeColor }} strokeWidth={1.75} />
            </span>
          ) : null}
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="stat-figure text-[26px] text-color-text">{value}</span>
          {unit ? <span className="text-sm text-text-faint">{unit}</span> : null}
        </div>
      </div>

      <div className="flex items-end justify-between gap-4">
        <div>
          {trend !== undefined ? (
            <div
              className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
              style={{
                color: trendPositive ? "var(--ok)" : "var(--crit)",
                background: trendPositive ? "var(--ok-soft)" : "var(--crit-soft)",
              }}
            >
              {trendPositive ? <ArrowUpRight size={12} strokeWidth={2.5} /> : <ArrowDownRight size={12} strokeWidth={2.5} />}
              {Math.abs(trend)}%
            </div>
          ) : null}
          {trendLabel ? <div className="mt-2 text-xs text-text-faint">{trendLabel}</div> : null}
        </div>
        {sparklineData ? (
          <div className="h-12 w-[120px]">
            <SparklineChart data={sparklineData} color={sparklineColor ?? "var(--accent)"} />
          </div>
        ) : null}
      </div>
    </Card>
  );
}
