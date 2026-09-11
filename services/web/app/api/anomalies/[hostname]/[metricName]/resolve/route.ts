import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ hostname: string; metricName: string }> }
) {
  const { hostname, metricName } = await params;
  const res = await fetch(
    `${API_URL}/api/v1/anomalies/${encodeURIComponent(hostname)}/${encodeURIComponent(metricName)}/resolve`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: await request.text(),
      cache: "no-store",
    }
  );
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
