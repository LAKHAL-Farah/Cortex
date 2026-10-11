import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = { params: Promise<{ traceId: string }> };

/** One incident, diagnose -> propose -> validate -> execute (roadmap 4.8). */
export async function GET(_req: Request, { params }: Context) {
  const { traceId } = await params;
  const res = await fetch(`${API_URL}/api/v1/remediation/audit/incidents/${encodeURIComponent(traceId)}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
