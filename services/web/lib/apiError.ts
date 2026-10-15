/** Turns an API error `detail` into a string that is safe to render.
 *
 * FastAPI returns `detail` as a plain string for HTTPException, but as a LIST
 * of objects ({type, loc, msg, input, ctx}) for a 422 validation error.
 * Putting that list straight into React state and rendering it crashes the
 * page (React error #31, "Objects are not valid as a React child"). */
export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) =>
        d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : "",
      )
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  return fallback;
}
