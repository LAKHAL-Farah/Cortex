import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Ctx = { params: Promise<{ projectId: string }> };

export async function GET(_request: NextRequest, { params }: Ctx) {
  const { projectId } = await params;
  const res = await fetch(`${API_URL}/api/v1/quotas/projects/${encodeURIComponent(projectId)}/settings`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}

/** Department / monthly budget for one project (admin-only upstream). */
export async function PUT(request: NextRequest, { params }: Ctx) {
  const { projectId } = await params;
  const res = await fetch(`${API_URL}/api/v1/quotas/projects/${encodeURIComponent(projectId)}/settings`, {
    method: "PUT",
    headers: await authHeaders({ "content-type": "application/json" }),
    body: await request.text(),
    cache: "no-store",
  });
  const data = await res.json().catch(() => null);
  return NextResponse.json(data, { status: res.status });
}
