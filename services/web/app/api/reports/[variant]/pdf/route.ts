import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

const VARIANTS = ["executive", "financial", "technical"];
const ALLOWED = ["period", "ai", "rightsizing_source"];

type Ctx = { params: Promise<{ variant: string }> };

/** Binary pass-through of an on-demand report PDF (executive / financial /
 * technical). The browser can't send the httpOnly session cookie to the API
 * itself, so the download goes through here like every other app/api route. */
export async function GET(request: NextRequest, { params }: Ctx) {
  const { variant } = await params;
  if (!VARIANTS.includes(variant)) {
    return NextResponse.json({ detail: `Unknown report '${variant}'` }, { status: 404 });
  }
  const qs = new URLSearchParams();
  for (const key of ALLOWED) {
    const value = request.nextUrl.searchParams.get(key);
    if (value) qs.set(key, value);
  }
  const res = await fetch(`${API_URL}/api/v1/reports/${variant}.pdf${qs.size ? `?${qs}` : ""}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    return NextResponse.json(data ?? { detail: "Report generation failed" }, { status: res.status });
  }
  return new NextResponse(await res.arrayBuffer(), {
    status: 200,
    headers: {
      "content-type": "application/pdf",
      "content-disposition": res.headers.get("content-disposition") ?? "attachment",
      "cache-control": "no-store",
    },
  });
}
