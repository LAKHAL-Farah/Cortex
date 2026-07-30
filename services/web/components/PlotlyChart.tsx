"use client";
import React, { useEffect, useState } from "react";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false }) as any;

type Point = { t: number; v: number };

function useThemeColors() {
  const [colors, setColors] = useState({ text: "#6B7280", grid: "rgba(148,163,184,0.15)" });

  useEffect(() => {
    const read = () => {
      const style = getComputedStyle(document.documentElement);
      setColors({
        text: style.getPropertyValue("--text-faint").trim() || "#6B7280",
        grid: style.getPropertyValue("--border-soft").trim() || "rgba(148,163,184,0.15)",
      });
    };
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return colors;
}

export default function PlotlyChart({ data, color, height = 260 }: { data: Point[]; color: string; height?: number }) {
  const { text, grid } = useThemeColors();
  const [resolvedColor, setResolvedColor] = useState(color);

  useEffect(() => {
    if (color.trim().startsWith("var(")) {
      const varName = color.trim().slice(4, -1);
      const read = () => setResolvedColor(getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || "#E15B3C");
      read();
      const observer = new MutationObserver(read);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
      return () => observer.disconnect();
    }
    setResolvedColor(color);
  }, [color]);

  const sorted = (data ?? []).slice().sort((a, b) => a.t - b.t);
  const x = sorted.map((p) => new Date(p.t * 1000));
  const y = sorted.map((p) => p.v);
  const latestIndex = y.length - 1;
  const fillColor = resolvedColor.startsWith("#")
    ? `${resolvedColor}1f`
    : resolvedColor.replace(")", ",0.12)").replace("rgb", "rgba");

  return (
    <div>
      <Plot
        data={[
          {
            x,
            y,
            type: "scatter",
            mode: "lines",
            line: { color: resolvedColor, width: 2, shape: "spline" },
            fill: "tozeroy",
            fillcolor: fillColor,
          },
          latestIndex >= 0 && {
            x: [x[latestIndex]],
            y: [y[latestIndex]],
            type: "scatter",
            mode: "markers",
            marker: { color: resolvedColor, size: 6 },
            hoverinfo: "skip",
          },
        ].filter(Boolean)}
        layout={{
          autosize: true,
          height,
          margin: { l: 36, r: 8, t: 8, b: 28 },
          font: { color: text, size: 11 },
          xaxis: { showgrid: false, showline: false, tickformat: "%H:%M", zeroline: false, color: text },
          yaxis: { range: [0, 100], showgrid: true, gridcolor: grid, zeroline: false, color: text },
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
          hovermode: "x unified",
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
        useResizeHandler
      />
    </div>
  );
}
