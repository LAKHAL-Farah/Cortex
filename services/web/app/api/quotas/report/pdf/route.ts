import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Binary pass-through of the PDF export -- the browser can't send the
 * httpOnly session cookie to the API itself, so the download goes through
 * here like every other app/api route. */
export async function GET(request: NextRequest) {
  const period = request.nextUrl.searchParams.get("period");
  const qs = period ? `?period=${encodeURIComponent(period)}` : "";
  const res = await fetch(`${API_URL}/api/v1/quotas/report.pdf${qs}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    return NextResponse.json(data ?? { detail: "Export failed" }, { status: res.status });
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
