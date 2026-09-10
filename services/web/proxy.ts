import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/serverAuth";

/** Cheap, presence-only gate: real enforcement happens on every request in
 * services/api (Depends(get_current_user)/require_admin, see
 * services/api/app/auth.py) since that's the trust boundary that actually
 * matters -- an expired or tampered token still gets a 401 there even if
 * the cookie is present. This middleware just avoids flashing protected
 * pages before that 401 round-trips, by bouncing straight to /login when
 * there's obviously no session at all. */
export function proxy(req: NextRequest) {
  const hasSession = req.cookies.has(SESSION_COOKIE);

  if (!hasSession) {
    const loginUrl = new URL("/login", req.url);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match every route except:
     * - /login (the page you'd be redirected to -- can't require a session to reach it)
     * - /api/auth/login (called by the login page itself, before a session exists)
     * - Next.js internals and static assets
     */
    "/((?!login|api/auth/login|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
