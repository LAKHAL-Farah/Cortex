import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Proxies the "Rescan" control in the Security pages'
 * (SecurityRescanButton.tsx) to POST /api/v1/security/resync -- the
 * Security-page sibling of app/api/topology/resync/route.ts. */
export async function POST() {
  const res = await fetch(`${API_URL}/api/v1/security/resync`, {
    method: "POST",
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
