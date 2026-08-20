import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = {
  params: Promise<{ id: string }>;
};

export async function GET(req: Request, { params }: Context) {
  const { id } = await params;
  const clientId = req.headers.get("x-client-id") ?? "";
  const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
    headers: await authHeaders({ "X-Client-Id": clientId }),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function PUT(req: Request, { params }: Context) {
  const { id } = await params;
  const clientId = req.headers.get("x-client-id") ?? "";
  const body = await req.json();
  const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
    method: "PUT",
    headers: await authHeaders({ "Content-Type": "application/json", "X-Client-Id": clientId }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function DELETE(req: Request, { params }: Context) {
  const { id } = await params;
  const clientId = req.headers.get("x-client-id") ?? "";
  const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
    method: "DELETE",
    headers: await authHeaders({ "X-Client-Id": clientId }),
  });
  return new NextResponse(null, { status: res.status });
}
