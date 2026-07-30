"use client";
import React from "react";
import dynamic from "next/dynamic";

// react-plotly.js must be imported dynamically to avoid SSR issues
const Plot = dynamic(() => import("react-plotly.js"), { ssr: false }) as any;

type Point = { t: number; v: number };

export default function PlotlyChart({ data, color, height = 260 }: { data: Point[]; color: string; height?: number }) {
  const sorted = (data ?? []).slice().sort((a,b) => a.t - b.t);
  const x = sorted.map((p) => new Date(p.t * 1000));
  const y = sorted.map((p) => p.v);
  const latestIndex = y.length - 1;

  return (
    <div>
      <Plot
        data={[
          {
            x,
            y,
            type: 'scatter',
            mode: 'lines',
            line: { color, width: 3, shape: 'spline' },
            fill: 'tozeroy',
            fillcolor: color.replace(')', ',0.12)').replace('rgb', 'rgba'),
          },
          latestIndex >= 0 && {
            x: [x[latestIndex]],
            y: [y[latestIndex]],
            type: 'scatter',
            mode: 'markers',
            marker: { color, size: 7 },
            hoverinfo: 'skip',
          },
        ].filter(Boolean)}
        layout={{
          autosize: true,
          height,
          margin: { l: 40, r: 14, t: 12, b: 36 },
          xaxis: { showgrid: false, showline: false, tickformat: '%H:%M', zeroline: false },
          yaxis: { range: [0, 100], showgrid: false, zeroline: false },
          paper_bgcolor: 'transparent',
          plot_bgcolor: 'transparent',
          hovermode: 'x unified',
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%' }}
      />
    </div>
  );
}
