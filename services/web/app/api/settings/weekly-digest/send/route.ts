import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

// Builds the digest from live data (incl. one NVIDIA NIM call) before sending,
// so it can take noticeably longer than a normal settings request.
export const maxDuration = 120;

export async function POST() {
  const res = await fetch(`${API_URL}/api/v1/settings/weekly-digest/send`, {
    method: "POST",
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
