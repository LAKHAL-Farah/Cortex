import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Proxies the copilot chat widget (components/CopilotChat.tsx) to
 * POST /api/v1/knowledge/chat. Unlike every other route in app/api/ -- see
 * app/api/logs/route.ts for the plain-JSON shape -- this one passes the
 * upstream body straight through instead of awaiting res.json(), since the
 * API responds with a Server-Sent Events stream (adr-0005) that the client
 * reads incrementally. The Authorization header is attached here, server-
 * side, from the httpOnly session cookie (lib/serverAuth.ts), so it's never
 * sent to the browser. */
export async function POST(req: Request) {
  const body = await req.json();

  const upstream = await fetch(`${API_URL}/api/v1/knowledge/chat`, {
    method: "POST",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });

  if (!upstream.ok || !upstream.body) {
    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(data, { status: upstream.status });
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  });
}
