"use client";
import { useEffect, useRef } from "react";

const HISTORY_LEN = 30;
const history: Record<string, number[]> = {};

export default function Sparkline({ id, value, color }: { id: string; value: number; color: string }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const arr = (history[id] ??= []);
    arr.push(value);
    if (arr.length > HISTORY_LEN) arr.shift();

    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);
    const max = Math.max(...arr, 1);
    ctx.beginPath();
    arr.forEach((v, i) => {
      const x = (i / (HISTORY_LEN - 1)) * width;
      const y = height - (v / max) * height;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }, [value, id, color]);

  return <canvas ref={ref} width={90} height={30} />;
}