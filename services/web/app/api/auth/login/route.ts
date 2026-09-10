import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** The only unauthenticated route in app/api/ -- everything else requires
 * the cookie this sets. Proxies straight to POST /api/v1/auth/login, then
 * moves the returned JWT into an httpOnly cookie so the browser (and any
 * client-side JS/XSS) never gets to read the token itself; only this
 * server can, via lib/serverAuth.ts's authHeaders(). */
export async function POST(req: Request) {
  const body = await req.json();

  const upstream = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    return NextResponse.json(data, { status: upstream.status });
  }

  const res = NextResponse.json({ user: data.user });
  res.cookies.set(SESSION_COOKIE, data.access_token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    // Matches the API's own token TTL (CORTEX_JWT_TTL_MINUTES, default 8h)
    // -- no point keeping a cookie around after the token it holds has
    // expired.
    maxAge: 60 * 60 * 8,
  });
  return res;
}
