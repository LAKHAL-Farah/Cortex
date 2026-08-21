import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Every checked slot (including "normal") for one project -- backs the
 * per-project detail drawer, which needs full headroom, not just breaches. */
export async function GET(_req: Request, { params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  const res = await fetch(`${API_URL}/api/v1/quotas/alerts/${encodeURIComponent(projectId)}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => []);
  return NextResponse.json(data, { status: res.status });
}
