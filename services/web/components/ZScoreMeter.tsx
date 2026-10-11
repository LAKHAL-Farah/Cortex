"use client";

import { SEVERITY_COLOR } from "@/lib/anomalies";
import type { AnomalySeverity } from "@/lib/types";
import { useDetectionThresholds } from "@/lib/useDetectionThresholds";

/** Z-score bar with markers at the detector's *current* medium / high /
 * critical cut-offs, so it lines up with what actually produced the alert even
 * after an admin changes the sensitivity. */
export default function ZScoreMeter({ z, severity }: { z: number; severity: AnomalySeverity }) {
  const t = useDetectionThresholds();
  const scale = t.critical + 1.5;
  const fill = Math.min(Math.abs(z) / scale, 1);
  const fmt = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(1)}σ`;
  const markers = [
    { at: t.medium / scale, label: fmt(t.medium) },
    { at: t.high / scale, label: fmt(t.high) },
    { at: t.critical / scale, label: fmt(t.critical) },
  ];
  return (
    <div className="relative pt-1">
      <div className="relative h-2 overflow-hidden rounded-full" style={{ background: "var(--border-soft)" }}>
        <div
          className="h-full rounded-full transition-[width] duration-700 ease-out"
          style={{ width: `${fill * 100}%`, background: SEVERITY_COLOR[severity] }}
        />
        {markers.map((m, i) => (
          <div
            key={i}
            className="absolute top-0 h-full w-px"
            style={{ left: `${m.at * 100}%`, background: "var(--surface)", opacity: 0.6 }}
          />
        ))}
      </div>
      <div className="relative mt-1 h-3.5 text-[10px] text-text-faint">
        {markers.map((m, i) => (
          <span key={i} className="absolute -translate-x-1/2" style={{ left: `${m.at * 100}%` }}>
            {m.label}
          </span>
        ))}
      </div>
    </div>
  );
}
