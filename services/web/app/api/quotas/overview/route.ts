import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Cloud capacity/cost + every project's usage and cost -- see
 * services/api/app/routers/quotas.py::finops_overview. */
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/quotas/overview`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
