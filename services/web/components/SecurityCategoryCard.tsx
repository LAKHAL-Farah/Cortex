"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface SecurityCategory {
  label: string;
  description: string;
  href: string;
  icon: LucideIcon;
  color: string;
  available: boolean;
}

/** Phase Sec-5: "Browse by category" tile, reworked from the compact
 * .tile-card row into a bigger integrations-marketplace-style card (see
 * that reference screenshot) -- a colored gradient wash + oversized ghost
 * icon (.category-card in globals.css), a big solid icon chip instead of
 * a small one, a title with a one-line description of what that category
 * actually checks, and a footer status chip instead of a fake toggle. */
export default function SecurityCategoryCard({ category }: { category: SecurityCategory }) {
  const { label, description, href, icon: Icon, color, available } = category;

  return (
    <Link href={href} className="block h-full">
      <div
        className="category-card category-card--interactive flex h-full flex-col gap-4 p-5"
        style={{ ["--card-color" as string]: color }}
      >
        <Icon className="category-card__ghost-icon" strokeWidth={1} aria-hidden="true" />

        <div className="relative flex items-start justify-between gap-3">
          <span
            className="grid h-12 w-12 flex-shrink-0 place-items-center rounded-2xl"
            style={{
              background: `color-mix(in srgb, ${color} 18%, var(--surface))`,
              boxShadow: `0 0 0 1px color-mix(in srgb, ${color} 26%, transparent), 0 10px 22px -10px color-mix(in srgb, ${color} 55%, transparent)`,
            }}
          >
            <Icon className="h-6 w-6" style={{ color }} strokeWidth={2} />
          </span>
          <span
            className="shrink-0 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.06em]"
            style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
          >
            {available ? "Live" : "Preview"}
          </span>
        </div>

        <div className="relative">
          <div className="font-display text-[16px] font-semibold text-color-text">{label}</div>
          <p className="mt-1.5 text-[13px] leading-snug text-text-faint">{description}</p>
        </div>

        <div
          className="relative mt-auto flex items-center justify-between border-t pt-3 text-xs"
          style={{ borderColor: "var(--border-soft)" }}
        >
          <span className="inline-flex items-center gap-1 font-medium text-text-dim">
            View category
            <ArrowRight className="h-3 w-3" strokeWidth={2} />
          </span>
          <span
            className="inline-flex items-center gap-1.5 font-medium"
            style={{ color: available ? "var(--ok)" : "var(--text-muted)" }}
          >
            <span className="status-dot" style={{ background: available ? "var(--ok)" : "var(--text-muted)" }} />
            {available ? "Available" : "Coming soon"}
          </span>
        </div>
      </div>
    </Link>
  );
}
