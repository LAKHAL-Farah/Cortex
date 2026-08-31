import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

export async function POST() {
  const res = await fetch(`${API_URL}/api/v1/settings/alert-email/test`, { method: "POST", cache: "no-store" });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
