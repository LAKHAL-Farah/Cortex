import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL;

export async function GET() {
  if (!API_URL) {
    return NextResponse.json({ detail: "CORTEX_API_URL is not configured" }, { status: 503 });
  }

  try {
    const res = await fetch(`${API_URL}/api/v1/network/health`, { cache: "no-store" });
    const body = await res.text();

    try {
      return NextResponse.json(JSON.parse(body), { status: res.status });
    } catch {
      return NextResponse.json(
        { detail: "Network health service returned an invalid response" },
        { status: 502 },
      );
    }
  } catch {
    return NextResponse.json({ detail: "Network health service is unavailable" }, { status: 502 });
  }
}
