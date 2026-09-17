import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Proxies the security-groups detail page's sandbox-only "Simulate
 * drift" panel to POST/DELETE /api/v1/security/sandbox/drift/{hostname}
 * (services/security_sandbox.py). Admin-only and CORTEX_ENV=sandbox-only
 * on the API side already -- this route is a thin pass-through, it
 * doesn't duplicate either check. */
export async function POST(_req: Request, { params }: { params: Promise<{ hostname: string }> }) {
  const { hostname } = await params;
  const res = await fetch(`${API_URL}/api/v1/security/sandbox/drift/${encodeURIComponent(hostname)}`, {
    method: "POST",
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

export async function DELETE(req: Request, { params }: { params: Promise<{ hostname: string }> }) {
  const { hostname } = await params;
  const ruleId = new URL(req.url).searchParams.get("rule_id");
  if (!ruleId) {
    return NextResponse.json({ detail: "rule_id query param is required" }, { status: 400 });
  }
  const res = await fetch(
    `${API_URL}/api/v1/security/sandbox/drift/${encodeURIComponent(hostname)}?rule_id=${encodeURIComponent(ruleId)}`,
    { method: "DELETE", cache: "no-store", headers: await authHeaders() }
  );
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
