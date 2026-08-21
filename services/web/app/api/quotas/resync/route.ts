import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Proxies the "Check now" control (QuotaResyncButton.tsx) to
 * POST /api/v1/quotas/resync -- same pattern as
 * app/api/topology/resync/route.ts. */
export async function POST() {
  const res = await fetch(`${API_URL}/api/v1/quotas/resync`, {
    method: "POST",
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
