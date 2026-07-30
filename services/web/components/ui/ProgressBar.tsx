import React from "react";

export function ProgressBar({ value, color }: { value: number; color?: string }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full" style={{ background: "var(--border-soft)" }}>
      <div
        className="h-full rounded-full transition-[width] duration-700 ease-out"
        style={{ width: `${Math.min(Math.max(value, 0), 100)}%`, background: color ?? "var(--accent)" }}
      />
    </div>
  );
}
