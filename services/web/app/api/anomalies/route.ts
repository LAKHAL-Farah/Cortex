import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/anomalies`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
