import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

type Context = {
  params: Promise<{ id: string }>;
};

/** The explicit Execute click on an approved proposal (roadmap 4.4).
 * Forwards only the confirmation and the digest of the proposal the person
 * was looking at -- never a command. The API runs what it stored when the fix
 * was approved, checks the role, the approval and the impact again, and
 * answers 202 once the run has started; the card then polls the proposal. */
export async function POST(req: Request, { params }: Context) {
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { confirm?: boolean; proposal_digest?: string };

  const res = await fetch(`${API_URL}/api/v1/remediation/proposals/${encodeURIComponent(id)}/execute`, {
    method: "POST",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ confirm: body.confirm === true, proposal_digest: body.proposal_digest }),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
