import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

/** Currently-breached quota/budget slots -- see
 * services/api/app/routers/quotas.py::list_quota_alerts. Thin pass-through,
 * same shape as every other route in app/api/anomalies|topology/. */
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/quotas/alerts`, { cache: "no-store" });
  const data = await res.json().catch(() => []);
  return NextResponse.json(data, { status: res.status });
}
