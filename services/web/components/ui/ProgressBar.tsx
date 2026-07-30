import React from "react";

export function ProgressBar({ value, color }: { value: number; color?: string }) {
  return (
    <div className="h-2.5 overflow-hidden rounded-full bg-[#F1F5F9]">
      <div
        className="h-full rounded-full"
        style={{ width: `${Math.min(Math.max(value, 0), 100)}%`, background: color ?? "#F97316" }}
      />
    </div>
  );
}
