import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;

type Context = {
  params: Promise<{ id: string }>;
};

export async function GET(_req: Request, { params }: Context) {
  const { id } = await params;

  const res = await fetch(`${API_URL}/api/v1/topology/nodes/${encodeURIComponent(id)}`, {
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
