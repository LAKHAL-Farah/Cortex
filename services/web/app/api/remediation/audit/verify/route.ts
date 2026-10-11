import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Re-derive the whole audit hash chain (roadmap 4.8). Admin/operator. */
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/remediation/audit/verify`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
