import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = { params: Promise<{ id: string }> };

export async function PATCH(req: Request, { params }: Context) {
  const { id } = await params;
  const body = await req.json();
  const res = await fetch(`${API_URL}/api/v1/auth/users/${id}`, {
    method: "PATCH",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
