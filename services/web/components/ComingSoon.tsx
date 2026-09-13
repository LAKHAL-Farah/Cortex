"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "./ui/Card";
import SecurityScopeTag, { type SecurityScope } from "./SecurityScopeTag";

/** Placeholder for a Security sub-page whose backend phase (Sec-3, Sec-4,
 * Sec-5, or the RBAC-audit-log view) hasn't shipped yet. Deliberately
 * plain -- no charts, no fake data -- per §6's own instruction not to
 * scaffold ahead of the backend that would give a page real content:
 * "don't scaffold empty pages ahead of the backend existing; add each
 * page in the same PR as its backend check." The route exists (so the
 * overview's category cards and the sidebar don't link to a 404) but
 * says exactly that, honestly, instead of implying data that isn't
 * there.
 *
 * `scope` (Phase 0) states up front which of Node/Instance/Identity this
 * not-yet-built check will read from, once it exists -- so "not built
 * yet" and "which layer will this even be about" are both answered on
 * the same screen instead of only the first.
 */
export default function ComingSoon({
  icon: Icon,
  title,
  blockedOn,
  scope,
}: {
  icon: LucideIcon;
  title: string;
  blockedOn: string;
  scope: SecurityScope;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <Link href="/security" className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
        <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
        Back to Security
      </Link>
      <Card className="flex flex-col items-center gap-3 py-12 text-center">
        <span
          className="inline-flex h-12 w-12 items-center justify-center rounded-[var(--radius-control)]"
          style={{ background: "var(--canvas)" }}
        >
          <Icon className="h-6 w-6 text-text-faint" strokeWidth={1.75} />
        </span>
        <div className="flex items-center gap-2">
          <div className="font-display text-[17px] font-semibold text-color-text">{title}</div>
          <SecurityScopeTag scope={scope} />
        </div>
        <p className="max-w-sm text-sm text-text-faint">
          Not available yet -- {blockedOn}. This page will fill in once that backend work ships, in the same PR
          rather than ahead of it.
        </p>
      </Card>
    </div>
  );
}
