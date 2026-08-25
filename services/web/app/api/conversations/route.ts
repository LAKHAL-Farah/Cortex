import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

// Conversation history is scoped server-side by the logged-in account (see
// services/api/app/routers/conversations.py's Depends(get_current_user)),
// via the same session cookie -> Authorization header every other route in
// app/api/ already forwards through authHeaders(). No client-supplied
// scoping id needed here anymore.
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/conversations`, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function POST(req: Request) {
  const body = await req.json();
  const res = await fetch(`${API_URL}/api/v1/conversations`, {
    method: "POST",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
