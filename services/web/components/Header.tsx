"use client";

import React from "react";
import { Search, Bell, RefreshCw, Sparkles } from "lucide-react";
import ThemeToggle from "./ThemeToggle";

function IconButton({
  onClick,
  title,
  children,
}: {
  onClick?: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className="relative inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-dim transition-colors hover:bg-[var(--canvas)]"
      style={{ border: "1px solid var(--border)" }}
    >
      {children}
    </button>
  );
}

export default function Header() {
  const [q, setQ] = React.useState("");

  const doRefresh = () => {
    window.dispatchEvent(new Event("cortex:refresh"));
  };

  const openChat = () => {
    window.location.href = "/copilot";
  };

  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="eyebrow">Workspace</div>
          <h1 className="font-display mt-1 text-xl font-semibold text-color-text">Welcome back, operator.</h1>
        </div>

        <div className="flex items-center gap-2">
          <IconButton title="Refresh data" onClick={doRefresh}>
            <RefreshCw className="h-[15px] w-[15px]" strokeWidth={1.75} />
          </IconButton>

          <IconButton title="Notifications">
            <Bell className="h-[15px] w-[15px]" strokeWidth={1.75} />
            <span
              className="absolute -right-1 -top-1 inline-flex h-4 min-w-[1rem] items-center justify-center rounded-full px-1 text-[10px] font-semibold text-white"
              style={{ background: "var(--crit)" }}
            >
              3
            </span>
          </IconButton>

          <button
            onClick={openChat}
            className="inline-flex h-9 items-center gap-2 rounded-[var(--radius-control)] px-3 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <Sparkles className="h-[15px] w-[15px]" style={{ color: "var(--accent)" }} strokeWidth={1.75} />
            Copilot
          </button>

          <ThemeToggle />

          <button
            className="inline-flex h-9 items-center gap-2 rounded-[var(--radius-control)] px-3 text-sm font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <span
              className="grid h-5 w-5 place-items-center rounded-full text-[10px] font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              A
            </span>
            Alex
          </button>
        </div>
      </div>

      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search the cockpit..."
          className="w-full rounded-[var(--radius-control)] py-2.5 pl-9 pr-4 text-sm text-color-text outline-none transition-colors"
          style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
          onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
          onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
          aria-label="Global search"
        />
      </div>
    </header>
  );
}
