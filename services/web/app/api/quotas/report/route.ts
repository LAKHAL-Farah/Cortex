import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Showback/chargeback report for `?period=YYYY-MM` (default: this month). */
export async function GET(request: NextRequest) {
  const period = request.nextUrl.searchParams.get("period");
  const qs = period ? `?period=${encodeURIComponent(period)}` : "";
  const res = await fetch(`${API_URL}/api/v1/quotas/report${qs}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
