import { NextResponse } from "next/server";

const API_URL = process.env.CORTEX_API_URL!;
const API_KEY = process.env.CORTEX_API_KEY!;

/** Proxies the copilot chat widget (components/CopilotChat.tsx) to
 * POST /api/v1/knowledge/chat. Unlike every other route in app/api/ -- see
 * app/api/logs/route.ts for the plain-JSON shape -- this one passes the
 * upstream body straight through instead of awaiting res.json(), since the
 * API responds with a Server-Sent Events stream (adr-0005) that the client
 * reads incrementally. The X-API-Key header is attached here, server-side,
 * same as app/api/nodes/route.ts's POST, so it's never sent to the browser. */
export async function POST(req: Request) {
  const body = await req.json();

  const upstream = await fetch(`${API_URL}/api/v1/knowledge/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
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
