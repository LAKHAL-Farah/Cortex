import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export async function GET(req: NextRequest) {
  // Forwards ?hours=<n> straight through -- routers/security.py's
  // get_security_audit_log defaults it to 24 if omitted, same default
  // this page's own useSWR call relies on. Same req.nextUrl.search
  // passthrough app/api/logs/route.ts already uses.
  const qs = req.nextUrl.search;
  const res = await fetch(`${API_URL}/api/v1/security/audit-log${qs}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
