import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Backs SecurityHealthBadge.tsx. Same thin pass-through shape as
 * app/api/topology/health/route.ts, just for GET /api/v1/security/health
 * (models.SecurityScanRun) instead of the topology sync-runs table. */
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/security/health`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
