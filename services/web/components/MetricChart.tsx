"use client";
import { useEffect, useRef, useState } from "react";
import { AreaChart, Area, ResponsiveContainer, YAxis } from "recharts";

type Point = { t: number; v: number };

export function useMetricHistory(instance: string, metric: string, liveValue?: number, minutes = 60) {
  const [series, setSeries] = useState<Point[]>([]);
  const hydrated = useRef(false);

  useEffect(() => {
    hydrated.current = false;
    fetch(`/api/nodes/${encodeURIComponent(instance)}/history?minutes=${minutes}`)
      .then((r) => r.json())
      .then((data) => { setSeries(data[metric] ?? []); hydrated.current = true; })
      .catch(() => { hydrated.current = true; });
  }, [instance, metric, minutes]);

  useEffect(() => {
    if (!hydrated.current || liveValue == null) return;
    setSeries((prev) => {
      const t = Math.floor(Date.now() / 1000);
      if (prev.length && t - prev[prev.length - 1].t < 5) return prev; // dedup near-simultaneous ticks
      return [...prev, { t, v: liveValue }];
    });
  }, [liveValue]);

  return series;
}

export function MetricChart({ data, color }: { data: Point[]; color: string }) {
  return (
    <ResponsiveContainer width="100%" height={40}>
      <AreaChart data={data}>
        <YAxis hide domain={[0, 100]} />
        <Area dataKey="v" stroke={color} fill={color} fillOpacity={0.1} strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}