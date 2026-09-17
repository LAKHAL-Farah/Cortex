import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(_req: Request, { params }: { params: Promise<{ instance_id: string }> }) {
  const { instance_id } = await params;
  const res = await fetch(`${API_URL}/api/v1/security/instance-exposed-ports/${encodeURIComponent(instance_id)}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
