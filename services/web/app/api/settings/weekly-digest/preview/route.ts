import { NextRequest, NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

export const maxDuration = 120;

/** Passes the backend's rendered digest HTML straight through (the component
 * shows it in a sandboxed iframe). `?use_ai=false` skips the LLM call. */
export async function GET(request: NextRequest) {
  const useAi = request.nextUrl.searchParams.get("use_ai") === "false" ? "false" : "true";
  const res = await fetch(`${API_URL}/api/v1/settings/weekly-digest/preview?use_ai=${useAi}`, {
    cache: "no-store",
    headers: await authHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    return NextResponse.json(err ?? { detail: "Unable to build the preview." }, { status: res.status });
  }
  return new NextResponse(await res.text(), {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}
