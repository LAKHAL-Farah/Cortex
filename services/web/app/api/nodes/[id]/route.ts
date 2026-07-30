import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;
const API_KEY = process.env.CORTEX_API_KEY!;

type Context = {
  params: Promise<{ id: string }>;
};

export async function DELETE(
  _req: Request,
  { params }: Context
) {
  const { id } = await params;

  const res = await fetch(`${API_URL}/api/v1/nodes/${id}`, {
    method: "DELETE",
    headers: {
      "X-API-Key": API_KEY,
    },
  });

  return new NextResponse(null, {
    status: res.status,
  });
}


export async function PUT(
  req: Request,
  { params }: Context
) {
  const { id } = await params;

  const body = await req.json();

  const res = await fetch(`${API_URL}/api/v1/nodes/${id}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify(body),
  });

  const data = await res.json().catch(() => ({}));

  return NextResponse.json(data, {
    status: res.status,
  });
}