"use client";

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Card } from "./Card";
import SparklineChart from "./SparklineChart";
import { TrendingUp, TrendingDown } from "lucide-react";

export default function MetricCard({
  title,
  value,
  unit,
  trend,
  trendLabel,
  sparklineData,
  sparklineColor,
}: {
  title: string;
  value: string | number;
  unit?: string;
  trend?: number;
  trendLabel?: string;
  sparklineData?: { name: string; value: number }[];
  sparklineColor?: string;
}) {
  const trendPositive = (trend ?? 0) >= 0;
  return (
    <Card className="relative overflow-hidden p-6">
      <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-orange-300 via-orange-200 to-orange-100 opacity-50" />
      <div className="relative flex h-full flex-col justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-color-text">{title}</div>
          <div className="mt-3 flex items-baseline gap-3">
            <span className="text-4xl font-semibold text-color-text">{value}</span>
            {unit ? <span className="text-sm text-text-faint">{unit}</span> : null}
          </div>
        </div>

        <div className="flex items-end justify-between gap-4">
          <div>
            {trend !== undefined ? (
              <div className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ${
                trendPositive ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
              }`}>
                {trendPositive ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                {Math.abs(trend)}%
              </div>
            ) : null}
            {trendLabel ? <div className="mt-3 text-sm text-text-faint">{trendLabel}</div> : null}
          </div>
          {sparklineData ? (
            <div className="h-14 w-[140px]">
              <SparklineChart data={sparklineData} color={sparklineColor ?? '#F97316'} />
            </div>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
