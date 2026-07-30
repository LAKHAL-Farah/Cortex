import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;
const API_KEY = process.env.CORTEX_API_KEY!;

export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/nodes`, { cache: "no-store" });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

export async function POST(req: Request) {
  const body = await req.json();
  const res = await fetch(`${API_URL}/api/v1/nodes`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}