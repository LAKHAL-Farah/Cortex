import Link from "next/link";
import { Filter, X } from "lucide-react";

/** Phase Sec-6 (cross-linking): shown on the four Security sub-pages that
 * accept `?host=<hostname>` (auth-activity, exposed-ports,
 * vulnerabilities, kernel-signals -- see lib/securityStatus.ts's
 * filterFindingsByHost) when that param is present, so landing here from
 * a node's "Security posture" section is legible as a filtered view, not
 * a page that mysteriously only shows one host. `clearHref` is the
 * page's own bare path (no query string) rather than "/security" -- this
 * clears the filter without also losing which sub-page you're on.
 */
export default function SecurityHostFilterBanner({ host, clearHref }: { host: string; clearHref: string }) {
  return (
    <div
      className="flex items-center gap-2 rounded-[var(--radius-control)] px-3 py-2 text-sm"
      style={{ background: "var(--accent-soft, var(--canvas))", border: "1px solid var(--border-soft)" }}
    >
      <Filter className="h-3.5 w-3.5 shrink-0 text-text-faint" strokeWidth={2} />
      <span className="text-text-dim">
        Filtered to <span className="font-medium text-color-text">{host}</span>
      </span>
      <Link
        href={clearHref}
        className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-text-faint underline underline-offset-2 hover:text-text-dim"
      >
        <X className="h-3 w-3" strokeWidth={2} />
        Clear
      </Link>
    </div>
  );
}
