import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Recent incidents that produced a fix proposal (roadmap 4.8). Admin/operator. */
export async function GET(request: NextRequest) {
  const qs = new URL(request.url).searchParams.toString();
  const res = await fetch(`${API_URL}/api/v1/remediation/audit/incidents${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
