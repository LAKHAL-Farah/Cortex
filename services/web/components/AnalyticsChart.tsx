"use client";

import { AreaChart, Area, ResponsiveContainer, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";

export type AnalyticsPoint = {
  name: string;
  cpu: number;
  memory: number;
};

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;

  return (
    <div className="panel p-3.5 text-sm text-color-text" style={{ boxShadow: "var(--shadow-hover)" }}>
      <div className="eyebrow">{label}</div>
      {payload.map((item: any) => (
        <div key={item.dataKey} className="mt-2 flex items-center gap-2.5">
          <span className="status-dot" style={{ background: item.stroke }} />
          <div>
            <span className="font-medium">{item.name}</span>{" "}
            <span className="stat-figure text-text-faint">{item.value}%</span>
          </div>
        </div>
      ))}
    </div>
  );
};

export default function AnalyticsChart({
  data,
  primaryColor = "var(--chart-1)",
  secondaryColor = "var(--chart-4)",
}: {
  data: AnalyticsPoint[];
  primaryColor?: string;
  secondaryColor?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={360}>
      <AreaChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
        <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
        <YAxis axisLine={false} tickLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
        <Tooltip content={<CustomTooltip />} cursor={{ stroke: 'var(--accent)', strokeWidth: 1, strokeDasharray: '4 4' }} />

        <defs>
          <linearGradient id="lineGradientPrimary" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={primaryColor} stopOpacity={0.24} />
            <stop offset="100%" stopColor={primaryColor} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="lineGradientSecondary" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={secondaryColor} stopOpacity={0.16} />
            <stop offset="100%" stopColor={secondaryColor} stopOpacity={0} />
          </linearGradient>
        </defs>

        <Area
          type="monotone"
          dataKey="cpu"
          stroke={primaryColor}
          strokeWidth={2}
          fill="url(#lineGradientPrimary)"
          dot={false}
          activeDot={{ r: 5, stroke: primaryColor, strokeWidth: 2, fill: '#fff' }}
        />
        <Area
          type="monotone"
          dataKey="memory"
          stroke={secondaryColor}
          strokeWidth={2}
          fill="url(#lineGradientSecondary)"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
