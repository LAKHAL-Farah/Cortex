import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";

/** Shared shell so every dashboard widget has the same header, link and
 * padding as the rest of the app's panels. */
export default function Widget({
  eyebrow,
  title,
  href,
  hrefLabel = "Open",
  right,
  children,
  className = "",
  flush = false,
  fill = false,
}: {
  eyebrow: string;
  title: string;
  href?: string;
  hrefLabel?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  flush?: boolean;
  /** Body becomes a flex column that fills the panel (for charts that should stretch). */
  fill?: boolean;
}) {
  return (
    <section className={`panel flex min-w-0 flex-col ${className}`}>
      <div className="flex items-start justify-between gap-3 px-4 pt-4">
        <div className="min-w-0">
          <div className="eyebrow">{eyebrow}</div>
          <h3 className="mt-1 truncate text-[15px] font-semibold text-color-text">{title}</h3>
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          {right}
          {href && (
            <Link href={href} className="inline-flex items-center gap-1 text-xs font-medium text-text-faint transition-colors hover:text-color-text">
              {hrefLabel}
              <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={2} />
            </Link>
          )}
        </div>
      </div>
      <div className={flush ? "mt-3 flex-1" : fill ? "flex flex-1 flex-col p-4 pt-3" : "flex-1 p-4 pt-3"}>{children}</div>
    </section>
  );
}

export function Pill({ color, soft, children }: { color: string; soft: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium" style={{ color, background: soft }}>
      <span className="status-dot" style={{ background: color }} />
      {children}
    </span>
  );
}

export function Skeleton({ h = 80 }: { h?: number }) {
  return <div className="animate-pulse rounded-[var(--radius-control)]" style={{ height: h, background: "var(--canvas)" }} />;
}

export function Unavailable({ text = "Data unavailable right now." }: { text?: string }) {
  return <div className="flex h-full min-h-[80px] items-center justify-center text-center text-xs text-text-faint">{text}</div>;
}

export function MiniStat({ label, value, color }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div className="rounded-[var(--radius-control)] px-3 py-2" style={{ background: "var(--canvas)" }}>
      <div className="stat-figure text-lg leading-6" style={{ color: color ?? "var(--text)" }}>{value}</div>
      <div className="text-[11px] text-text-faint">{label}</div>
    </div>
  );
}
