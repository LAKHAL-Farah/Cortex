import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Defaults, bounds and presets for the capacity what-if simulator
 * (roadmap 4.12) -- see services/api/app/routers/capacity.py. */
export async function GET() {
  const res = await fetch(`${API_URL}/api/v1/capacity/options`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
