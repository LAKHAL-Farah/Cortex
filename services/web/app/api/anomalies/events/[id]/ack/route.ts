import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = { params: Promise<{ id: string }> };

/** Acknowledge an alert (roadmap 4.7): stops its escalation. The role check
 * (admin / operator) happens upstream in the API, not here. */
export async function POST(_req: Request, { params }: Context) {
  const { id } = await params;
  const res = await fetch(`${API_URL}/api/v1/anomalies/events/${encodeURIComponent(id)}/ack`, {
    method: "POST",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
