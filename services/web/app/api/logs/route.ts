import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(req: NextRequest) {
  const qs = req.nextUrl.search;
  const res = await fetch(`${API_URL}/api/v1/logs${qs}`, { cache: "no-store" });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
