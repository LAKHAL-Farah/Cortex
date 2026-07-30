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
    <div className="rounded-[18px] border border-[#ECECEC] bg-white p-4 shadow-[0_8px_24px_rgba(0,0,0,0.08)] text-sm text-color-text">
      <div className="text-xs uppercase tracking-[0.2em] text-text-faint">{label}</div>
      {payload.map((item: any) => (
        <div key={item.dataKey} className="mt-3 flex items-center gap-3">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: item.stroke }} />
          <div>
            <div className="font-semibold">{item.name}</div>
            <div className="text-text-faint">{item.value}%</div>
          </div>
        </div>
      ))}
    </div>
  );
};

export default function AnalyticsChart({
  data,
  primaryColor = "#F97316",
  secondaryColor = "#7C3AED",
}: {
  data: AnalyticsPoint[];
  primaryColor?: string;
  secondaryColor?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={360}>
      <AreaChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="rgba(15,23,42,0.08)" strokeDasharray="3 6" vertical={false} />
        <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
        <YAxis axisLine={false} tickLine={false} tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
        <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#F97316', strokeWidth: 1, strokeDasharray: '4 4' }} />

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
