import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Proxies the upgraded Copilot (components/CopilotChat.tsx) to
 * POST /api/v1/agents/orchestrate -- the LangGraph router that picks a
 * monitoring / prediction / rag specialist per question (see
 * services/api/app/agents/). Unlike app/api/knowledge/chat/route.ts this
 * upstream call is a single JSON response, not an SSE stream (adr-0005 was
 * about the RAG-only chat; the orchestrator hasn't grown streaming yet --
 * see routers/agents.py), so this route mirrors the plain-JSON proxy
 * pattern used by app/api/dashboard/route.ts instead. */
export async function POST(req: Request) {
  const body = await req.json();

  const upstream = await fetch(`${API_URL}/api/v1/agents/orchestrate`, {
    method: "POST",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
