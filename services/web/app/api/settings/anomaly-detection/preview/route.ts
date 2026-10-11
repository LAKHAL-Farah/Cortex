import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Replays the last 7 days of alerts against candidate settings. Saves nothing. */
export async function POST(request: NextRequest) {
  const res = await fetch(`${API_URL}/api/v1/settings/anomaly-detection/preview`, {
    method: "POST",
    headers: await authHeaders({ "content-type": "application/json" }),
    body: await request.text(),
    cache: "no-store",
  });
  return NextResponse.json(await res.json().catch(() => ({})), { status: res.status });
}
