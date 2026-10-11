"use client";

import React from "react";
import Link from "next/link";
import { Bell, Siren, Wallet, CalendarClock, CheckCheck, CheckCircle2, ArrowRight } from "lucide-react";
import { useNotifications, type AppNotification, type NotificationKind, type NotificationLevel } from "@/lib/useNotifications";
import { formatRelative } from "@/lib/anomalies";

const LEVEL_COLOR: Record<NotificationLevel, string> = {
  critical: "var(--crit)",
  warning: "var(--warn)",
  info: "var(--medium)",
};
const LEVEL_SOFT: Record<NotificationLevel, string> = {
  critical: "var(--crit-soft)",
  warning: "var(--warn-soft)",
  info: "var(--medium-soft)",
};
const KIND_ICON: Record<NotificationKind, typeof Siren> = {
  incident: Siren,
  quota: Wallet,
  forecast: CalendarClock,
};
const FILTERS: { key: "all" | NotificationKind; label: string }[] = [
  { key: "all", label: "All" },
  { key: "incident", label: "Incidents" },
  { key: "quota", label: "Quotas" },
  { key: "forecast", label: "Forecast" },
];

function Row({ n, unread, onOpen }: { n: AppNotification; unread: boolean; onOpen: () => void }) {
  const Icon = KIND_ICON[n.kind];
  return (
    <Link
      href={n.href}
      onClick={onOpen}
      className="flex items-start gap-3 px-3 py-2.5 transition-colors hover:bg-[var(--canvas)]"
    >
      <div
        className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[var(--radius-control)]"
        style={{ background: LEVEL_SOFT[n.level] }}
      >
        <Icon className="h-[15px] w-[15px]" style={{ color: LEVEL_COLOR[n.level] }} strokeWidth={1.75} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-medium text-color-text">{n.title}</span>
          {unread && <span className="status-dot flex-shrink-0" style={{ background: "var(--accent)" }} />}
        </div>
        <div className="mt-0.5 line-clamp-2 text-xs text-text-faint">{n.detail}</div>
        {n.at && <div className="mt-1 text-[11px] text-text-muted">{formatRelative(n.at)}</div>}
      </div>
    </Link>
  );
}

export default function NotificationsMenu() {
  const { items, readIds, unreadCount, markRead, markAllRead, loading } = useNotifications();
  const [open, setOpen] = React.useState(false);
  const [filter, setFilter] = React.useState<(typeof FILTERS)[number]["key"]>("all");
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const visible = filter === "all" ? items : items.filter((n) => n.kind === filter);
  const hasCritical = items.some((n) => n.level === "critical" && !readIds.has(n.id));

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        title="Notifications"
        aria-label={`Notifications${unreadCount ? `, ${unreadCount} unread` : ""}`}
        aria-expanded={open}
        className="relative inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-dim transition-colors hover:bg-[var(--canvas)]"
        style={{ border: "1px solid var(--border)" }}
      >
        <Bell className="h-[15px] w-[15px]" strokeWidth={1.75} />
        {unreadCount > 0 && (
          <span
            className="absolute -right-1 -top-1 inline-flex h-4 min-w-[1rem] items-center justify-center rounded-full px-1 text-[10px] font-semibold text-white"
            style={{ background: hasCritical ? "var(--crit)" : "var(--accent)" }}
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="panel absolute right-0 z-50 mt-2 w-[380px] max-w-[calc(100vw-2rem)] overflow-hidden" style={{ boxShadow: "var(--shadow-hover)" }}>
          <div className="flex items-center justify-between px-3 py-2.5" style={{ borderBottom: "1px solid var(--border-soft)" }}>
            <div>
              <div className="eyebrow">Notifications</div>
              <div className="text-xs text-text-faint">{unreadCount ? `${unreadCount} unread` : "You're all caught up"}</div>
            </div>
            <button
              onClick={markAllRead}
              disabled={unreadCount === 0}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1 text-xs font-medium text-text-dim transition-colors hover:bg-[var(--canvas)] disabled:opacity-40"
            >
              <CheckCheck className="h-3.5 w-3.5" strokeWidth={1.75} />
              Mark all read
            </button>
          </div>

          <div className="flex gap-1 px-3 py-2" style={{ borderBottom: "1px solid var(--border-soft)" }}>
            {FILTERS.map((f) => {
              const active = filter === f.key;
              return (
                <button
                  key={f.key}
                  onClick={() => setFilter(f.key)}
                  className="rounded-full px-2.5 py-1 text-xs font-medium transition-colors"
                  style={{
                    background: active ? "var(--accent-soft)" : "transparent",
                    color: active ? "var(--accent)" : "var(--text-faint)",
                  }}
                >
                  {f.label}
                </button>
              );
            })}
          </div>

          <div className="max-h-[360px] divide-y overflow-y-auto" style={{ borderColor: "var(--border-soft)" }}>
            {visible.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
                <CheckCircle2 className="h-7 w-7" style={{ color: "var(--ok)" }} strokeWidth={1.5} />
                <div className="text-sm font-medium text-color-text">{loading ? "Checking for alerts…" : "Nothing needs attention"}</div>
                <div className="text-xs text-text-faint">Incidents, quota breaches and forecast warnings will show up here.</div>
              </div>
            ) : (
              visible.map((n) => <Row key={n.id} n={n} unread={!readIds.has(n.id)} onOpen={() => { markRead(n.id); setOpen(false); }} />)
            )}
          </div>

          <Link
            href="/alerts"
            onClick={() => setOpen(false)}
            className="flex items-center justify-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ borderTop: "1px solid var(--border-soft)", color: "var(--accent)" }}
          >
            View all alerts
            <ArrowRight className="h-3.5 w-3.5" strokeWidth={1.75} />
          </Link>
        </div>
      )}
    </div>
  );
}
