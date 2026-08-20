import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

// X-Client-Id scopes history to one anonymous browser (see
// lib/copilotHistory.ts's getClientId) -- it's not a secret, just an opaque
// id the browser already has, so it's fine to read it straight off the
// incoming request and forward it. The Authorization header (the logged-in
// user's session) comes from the httpOnly cookie server-side, same as every
// other route in app/api/, so it's never sent to the browser.
export async function GET(req: Request) {
  const clientId = req.headers.get("x-client-id") ?? "";
  const res = await fetch(`${API_URL}/api/v1/conversations`, {
    headers: await authHeaders({ "X-Client-Id": clientId }),
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
    headers: await authHeaders({ "Content-Type": "application/json", "X-Client-Id": clientId }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
