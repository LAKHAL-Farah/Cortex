import React from "react";

export default function RadialProgressCard({
  label,
  value,
  description,
  accentColor = "#F97316",
}: {
  label: string;
  value: number;
  description: string;
  accentColor?: string;
}) {
  const circumference = 2 * Math.PI * 46;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="rounded-[20px] border border-[#ECECEC] bg-white p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-text-faint">{label}</div>
          <div className="mt-4 text-3xl font-semibold text-color-text">{value}%</div>
          <p className="mt-2 text-sm leading-6 text-text-dim">{description}</p>
        </div>
        <div className="relative h-[120px] w-[120px]">
          <svg viewBox="0 0 120 120" className="h-full w-full">
            <circle
              cx="60"
              cy="60"
              r="46"
              fill="none"
              stroke="#F1F5F9"
              strokeWidth="12"
            />
            <circle
              cx="60"
              cy="60"
              r="46"
              fill="none"
              stroke={accentColor}
              strokeWidth="12"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              transform="rotate(-90 60 60)"
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="text-sm font-semibold text-color-text">{value}%</div>
          </div>
        </div>
      </div>
    </div>
  );
}
