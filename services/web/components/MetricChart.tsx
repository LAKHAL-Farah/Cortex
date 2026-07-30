"use client";
import { useEffect, useRef, useState } from "react";
import { AreaChart, Area, ResponsiveContainer, YAxis, XAxis, Tooltip } from "recharts";

type Point = { t: number; v: number };

export function useMetricHistory(instance: string, metric: string, liveValue?: number, minutes = 60) {
  const [series, setSeries] = useState<Point[]>([]);
  const hydrated = useRef(false);

  useEffect(() => {
    const cacheKey = `cortex:history:${instance}:${metric}`;
    let hasCache = false;

    // Load cached series first so charts show immediately on open
    try {
      const raw = localStorage.getItem(cacheKey);
      if (raw) {
        const parsed = JSON.parse(raw) as Point[];
        if (Array.isArray(parsed) && parsed.length) {
          setSeries(parsed.slice().sort((a,b) => a.t - b.t));
          hasCache = true;
          hydrated.current = true; // allow liveValue appends immediately
        }
      }
    } catch (e) {
      // ignore cache errors
    }

    const doFetch = async () => {
      try {
        const res = await fetch(`/api/nodes/${encodeURIComponent(instance)}/history?minutes=${minutes}`);
        const data = await res.json();
        const incoming = (data && data[metric]) ? (data[metric] as Point[]) : [];

        if (incoming && incoming.length) {
          const sorted = incoming.slice().sort((a,b) => a.t - b.t);
          setSeries(sorted);
          try { localStorage.setItem(cacheKey, JSON.stringify(sorted)); } catch (e) {}
        } else if (!hasCache && liveValue != null) {
          const now = Math.floor(Date.now() / 1000);
          const fallback = [
            { t: now - Math.min(minutes * 60, 300), v: liveValue },
            { t: now, v: liveValue },
          ];
          setSeries(fallback);
          try { localStorage.setItem(cacheKey, JSON.stringify(fallback)); } catch (e) {}
        }
      } catch {
        // ignore fetch failure, preserve cached series if available
      } finally {
        hydrated.current = true;
      }
    };

    doFetch();

    const onRefresh = () => doFetch();
    window.addEventListener("cortex:refresh", onRefresh);
    return () => window.removeEventListener("cortex:refresh", onRefresh);
  }, [instance, metric, minutes, liveValue]);

  useEffect(() => {
    if (!hydrated.current || liveValue == null) return;
    setSeries((prev) => {
      const t = Math.floor(Date.now() / 1000);
      if (prev.length && t - prev[prev.length - 1].t < 5) return prev; // dedup near-simultaneous ticks
      const next = [...prev, { t, v: liveValue }];
      try {
        const cacheKey = `cortex:history:${instance}:${metric}`;
        localStorage.setItem(cacheKey, JSON.stringify(next));
      } catch (e) {}
      return next;
    });
  }, [liveValue, instance, metric]);

  return series;
}

export function MetricChart({ data, color, height = 80, showXAxis = false }: { data: Point[]; color: string; height?: number; showXAxis?: boolean }) {
  const sorted = (data ?? []).slice().sort((a,b) => a.t - b.t);

  const gradientId = `g-${color.replace(/[^a-z0-9]/gi, '')}`;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={sorted} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.24} />
            <stop offset="100%" stopColor={color} stopOpacity={0.04} />
          </linearGradient>
        </defs>

        <XAxis
          dataKey="t"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(v) => new Date(v * 1000).toLocaleTimeString()}
          hide={!showXAxis}
          tick={{ fill: 'var(--color-text-dim)' }}
        />

        <YAxis hide domain={[0, 100]} />
        <Tooltip labelFormatter={(v:any) => new Date(v * 1000).toLocaleString()} formatter={(val:any) => [val, 'value']} />

        <Area
          dataKey="v"
          stroke={color}
          fill={`url(#${gradientId})`}
          strokeWidth={2}
          dot={false}
          type="monotone"
          isAnimationActive={true}
          animationDuration={400}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}