import { NextResponse } from "next/server";
const API_URL = process.env.CORTEX_API_URL!;

export async function GET(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params; // this is the `instance` string (ip:port), url-encoded
  const minutes = new URL(req.url).searchParams.get("minutes") ?? "60";
  const res = await fetch(`${API_URL}/api/v1/nodes/${id}/history?minutes=${minutes}`, { cache: "no-store" });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}