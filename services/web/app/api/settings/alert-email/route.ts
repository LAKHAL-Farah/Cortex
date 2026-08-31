import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/settings/alert-email`, { cache: "no-store" });
  return NextResponse.json(await res.json(), { status: res.status });
}

export async function PUT(request: NextRequest) {
  const res = await fetch(`${API_URL}/api/v1/settings/alert-email`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: await request.text(),
    cache: "no-store",
  });
  return NextResponse.json(await res.json(), { status: res.status });
}
