import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(_req: Request, { params }: { params: Promise<{ hostname: string }> }) {
  const { hostname } = await params;
  const res = await fetch(`${API_URL}/api/v1/security/groups/${encodeURIComponent(hostname)}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
