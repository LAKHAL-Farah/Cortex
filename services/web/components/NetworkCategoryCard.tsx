"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "./ui/Card";

export default function NetworkCategoryCard({
  href,
  label,
  description,
  count,
  color,
  icon: Icon,
}: {
  href: string;
  label: string;
  description: string;
  count: number;
  color: string;
  icon: LucideIcon;
}) {
  return (
    <Link href={href} className="block">
      <Card interactive padding="p-0" className="relative overflow-hidden">
        <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: color }} />
        <div className="flex items-start justify-between gap-3 p-5 pl-6">
          <div className="flex items-start gap-3">
            <span
              className="mt-0.5 inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
              style={{ background: `color-mix(in srgb, ${color} 14%, transparent)` }}
            >
              <Icon className="h-5 w-5" style={{ color }} strokeWidth={2} />
            </span>
            <div>
              <div className="font-display text-[16px] font-semibold text-color-text">{label}</div>
              <p className="mt-0.5 text-sm text-text-faint">{description}</p>
            </div>
          </div>
          <ArrowUpRight className="mt-1 h-4 w-4 flex-shrink-0 text-text-faint" strokeWidth={2} />
        </div>
        <div className="flex items-center justify-between border-t px-5 py-3 pl-6" style={{ borderColor: "var(--border-soft)" }}>
          <span className="text-xs text-text-faint">Synced from Neo4j</span>
          <span className="stat-figure text-lg" style={{ color }}>
            {count}
          </span>
        </div>
      </Card>
    </Link>
  );
}
