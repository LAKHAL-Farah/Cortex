import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(
  req: Request,
  { params }: { params: Promise<{ hostname: string; metric: string }> }
) {
  const { hostname, metric } = await params;
  const { searchParams } = new URL(req.url);
  const threshold = searchParams.get("threshold");
  const horizonDays = searchParams.get("horizon_days");
  const qsParams = new URLSearchParams();
  if (threshold) qsParams.set("threshold", threshold);
  if (horizonDays) qsParams.set("horizon_days", horizonDays);
  const qs = qsParams.toString() ? `?${qsParams.toString()}` : "";
  const res = await fetch(
    `${API_URL}/api/v1/forecast/${encodeURIComponent(hostname)}/${encodeURIComponent(metric)}/threshold${qs}`,
    { cache: "no-store", headers: await authHeaders() }
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
