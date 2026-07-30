import React from "react";

export default function RadialProgressCard({
  label,
  value,
  description,
  accentColor = "var(--accent)",
}: {
  label: string;
  value: number;
  description: string;
  accentColor?: string;
}) {
  const circumference = 2 * Math.PI * 42;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="panel p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="eyebrow">{label}</div>
          <div className="stat-figure mt-3 text-2xl text-color-text">{value}%</div>
          <p className="mt-2 text-sm leading-6 text-text-faint">{description}</p>
        </div>
        <div className="relative h-[104px] w-[104px] flex-shrink-0">
          <svg viewBox="0 0 104 104" className="h-full w-full">
            <circle cx="52" cy="52" r="42" fill="none" stroke="var(--border-soft)" strokeWidth="9" />
            <circle
              cx="52"
              cy="52"
              r="42"
              fill="none"
              stroke={accentColor}
              strokeWidth="9"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              transform="rotate(-90 52 52)"
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="stat-figure text-sm text-color-text">{value}%</div>
          </div>
        </div>
      </div>
    </div>
  );
}
