import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = {
  params: Promise<{ id: string }>;
};

/** Approve / reject / ask for more information on a proposal (roadmap 4.3).
 * Forwards only `decision` and `comment` -- the API decides on its own
 * stored snapshot of the proposal, so nothing about the command is taken
 * from the browser, and the role check happens upstream, not here. */
export async function POST(req: Request, { params }: Context) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { decision?: string; comment?: string };

  const res = await fetch(`${API_URL}/api/v1/remediation/proposals/${encodeURIComponent(id)}/decision`, {
    method: "POST",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ decision: body.decision, comment: body.comment }),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
