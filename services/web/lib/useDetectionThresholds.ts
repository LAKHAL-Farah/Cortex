"use client";

import useSWR from "swr";
import { SEVERITY_THRESHOLDS } from "@/lib/anomalies";

export type ZThresholds = { medium: number; high: number; critical: number };

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
};

/** The z-score cut-offs the detector is using right now (Settings -> anomaly
 * detection). Falls back to the balanced defaults while loading or if the
 * request fails, so the meters always render. */
export function useDetectionThresholds(): ZThresholds {
  const { data } = useSWR("/api/settings/anomaly-detection", fetcher, { revalidateOnFocus: false });
  const z = data?.effective?.z;
  if (z && typeof z.medium === "number" && typeof z.high === "number" && typeof z.critical === "number") return z;
  return SEVERITY_THRESHOLDS;
}
