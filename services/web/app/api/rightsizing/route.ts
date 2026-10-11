import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Query keys the browser may set. Anything else is dropped rather than
 * forwarded, so this route can't be used to reach other upstream params. */
const ALLOWED = ["window_days", "source", "project_id"];

/** VM right-sizing recommendations (read-only) -- see
 * services/api/app/routers/rightsizing.py. */
export async function GET(request: NextRequest) {
  const qs = new URLSearchParams();
  for (const key of ALLOWED) {
    const value = request.nextUrl.searchParams.get(key);
    if (value) qs.set(key, value);
  }
  const res = await fetch(`${API_URL}/api/v1/rightsizing${qs.size ? `?${qs}` : ""}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
