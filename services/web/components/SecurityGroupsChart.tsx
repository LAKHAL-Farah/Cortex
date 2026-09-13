"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { SecurityFinding } from "@/lib/types";

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="panel p-3.5 text-sm text-color-text" style={{ boxShadow: "var(--shadow-hover)" }}>
      <div className="eyebrow">{label}</div>
      {payload.map((item: any) => (
        <div key={item.dataKey} className="mt-2 flex items-center gap-2.5">
          <span className="status-dot" style={{ background: item.fill }} />
          <div>
            <span className="font-medium">{item.name}</span>{" "}
            <span className="stat-figure text-text-faint">{item.value}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

/** Phase Sec-5: "or view it as a chart" toggle target for
 * /security/security-groups -- same per-host risky-rules/drift counts the
 * table's last two columns show, as grouped bars, so a fleet-wide spike or
 * a single outlier host reads at a glance instead of scanning every row. */
export default function SecurityGroupsChart({ findings }: { findings: SecurityFinding[] }) {
  const data = findings.map((f) => ({
    name: f.hostname,
    risky: f.raw_data.sec_group_signal.risky_rules?.length ?? 0,
    drift: f.raw_data.sec_group_signal.drift?.length ?? 0,
  }));

  return (
    <div className="panel p-5">
      <ResponsiveContainer width="100%" height={360}>
        <BarChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border-soft)" strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: "var(--text-muted)", fontSize: 12 }} />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: "var(--canvas)" }} />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-dim)" }} />
          <Bar dataKey="risky" name="Risky rules" fill="var(--crit)" radius={[4, 4, 0, 0]} />
          <Bar dataKey="drift" name="Changed since snapshot" fill="var(--warn)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
