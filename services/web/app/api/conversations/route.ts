import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;
const API_KEY = process.env.CORTEX_API_KEY!;

// X-Client-Id scopes history to one anonymous browser (see
// lib/copilotHistory.ts's getClientId) -- it's not a secret, just an opaque
// id the browser already has, so it's fine to read it straight off the
// incoming request and forward it. X-API-Key stays server-side only, same
// as every other route in app/api/, so it's never sent to the browser.
export async function GET(req: Request) {
  const clientId = req.headers.get("x-client-id") ?? "";
  const res = await fetch(`${API_URL}/api/v1/conversations`, {
    headers: { "X-API-Key": API_KEY, "X-Client-Id": clientId },
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function POST(req: Request) {
  const clientId = req.headers.get("x-client-id") ?? "";
  const body = await req.json();
  const res = await fetch(`${API_URL}/api/v1/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY, "X-Client-Id": clientId },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
