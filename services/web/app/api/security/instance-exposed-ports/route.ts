import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Fleet-wide variant of ./[instance_id]/route.ts -- proxies GET
 * /api/v1/security/instance-exposed-ports (no path param), which checks
 * every instance the topology graph knows about, not just one. Backs
 * InstanceExposureTable.tsx, the table that replaced the earlier one-
 * instance-at-a-time lookup UI (./[instance_id]/route.ts's endpoint is
 * kept as a standalone API surface -- e.g. for chat/agent use -- even
 * though no page component calls it directly anymore).
 */
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/security/instance-exposed-ports`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
