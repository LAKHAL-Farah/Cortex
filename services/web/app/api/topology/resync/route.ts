import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Proxies the "Reconverge" control in the topology top bar
 * (TopologyResyncButton.tsx) to POST /api/v1/topology/resync. Same
 * thin pass-through shape as every other route in app/api/topology/ --
 * see app/api/topology/health/route.ts. */
export async function POST() {
  const res = await fetch(`${API_URL}/api/v1/topology/resync`, {
    method: "POST",
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
