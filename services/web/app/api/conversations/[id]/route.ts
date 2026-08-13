import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;
const API_KEY = process.env.CORTEX_API_KEY!;

type Context = {
  params: Promise<{ id: string }>;
};

export async function GET(req: Request, { params }: Context) {
  const { id } = await params;
  const clientId = req.headers.get("x-client-id") ?? "";
  const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
    headers: { "X-API-Key": API_KEY, "X-Client-Id": clientId },
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
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY, "X-Client-Id": clientId },
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
    headers: { "X-API-Key": API_KEY, "X-Client-Id": clientId },
  });
  return new NextResponse(null, { status: res.status });
}
