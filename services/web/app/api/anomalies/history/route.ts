import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const qs = searchParams.toString();
  const res = await fetch(`${API_URL}/api/v1/anomalies/history${qs ? `?${qs}` : ""}`, { cache: "no-store" });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
