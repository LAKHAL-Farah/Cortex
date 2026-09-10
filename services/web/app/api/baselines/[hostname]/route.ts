import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(req: Request, { params }: { params: Promise<{ hostname: string }> }) {
  const { hostname } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const res = await fetch(`${API_URL}/api/v1/baselines/${encodeURIComponent(hostname)}${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
