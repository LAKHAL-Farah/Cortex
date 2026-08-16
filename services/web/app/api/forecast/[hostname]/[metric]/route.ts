import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(
  req: Request,
  { params }: { params: Promise<{ hostname: string; metric: string }> }
) {
  const { hostname, metric } = await params;
  const horizonDays = new URL(req.url).searchParams.get("horizon_days");
  const qs = horizonDays ? `?horizon_days=${encodeURIComponent(horizonDays)}` : "";
  const res = await fetch(
    `${API_URL}/api/v1/forecast/${encodeURIComponent(hostname)}/${encodeURIComponent(metric)}${qs}`,
    { cache: "no-store" }
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
