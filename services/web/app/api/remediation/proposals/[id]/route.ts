import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = {
  params: Promise<{ id: string }>;
};

/** Live status + decision history of one remediation proposal (roadmap 4.3).
 * The chat message only stores the proposal as it was when the turn ran, so
 * the approval card reads this to show what has happened to it since. */
export async function GET(_req: Request, { params }: Context) {
  const { id } = await params;
  const res = await fetch(`${API_URL}/api/v1/remediation/proposals/${encodeURIComponent(id)}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
