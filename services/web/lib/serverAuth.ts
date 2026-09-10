import { cookies } from "next/headers";

/** Name of the httpOnly cookie Next.js sets after a successful login (see
 * app/api/auth/login/route.ts). The browser can't read it or forward it
 * itself -- every app/api/* route handler in this app reads it server-side
 * via authHeaders() below and attaches it to the upstream FastAPI call as
 * a normal Authorization header. The JWT itself is never sent to the
 * browser as JS-readable data. */
export const SESSION_COOKIE = "cortex_session";

/** Builds the headers object for a server-side fetch to the Cortex API,
 * merging in `Authorization: Bearer <token>` from the session cookie (if
 * present) alongside whatever other headers the caller needs.
 *
 * Every route in app/api/ should use this instead of hand-building headers
 * -- previously most of them attached a single shared X-API-Key read from
 * process.env; now each request carries the actual logged-in user's token,
 * so the API can tell who's doing what and enforce per-role access
 * (see services/api/app/auth.py).
 */
export async function authHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  return {
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}
