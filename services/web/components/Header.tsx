"use client";

import React from "react";
import Link from "next/link";
import { RefreshCw, Sparkles } from "lucide-react";
import ThemeToggle from "./ThemeToggle";
import NotificationsMenu from "./NotificationsMenu";
import GlobalSearch from "./GlobalSearch";
import { useCurrentUser } from "@/lib/useCurrentUser";

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
  const { user } = useCurrentUser();
  const username = user?.username ?? "";
  const initial = username ? username.charAt(0).toUpperCase() : "·";

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
          <h1 className="font-display mt-1 text-xl font-semibold text-color-text">{username ? `Welcome back, ${username}.` : "Welcome back."}</h1>
        </div>

        <div className="flex items-center gap-2">
          <IconButton title="Refresh data" onClick={doRefresh}>
            <RefreshCw className="h-[15px] w-[15px]" strokeWidth={1.75} />
          </IconButton>

          <NotificationsMenu />

          <button
            onClick={openChat}
            className="inline-flex h-9 items-center gap-2 rounded-[var(--radius-control)] px-3 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <Sparkles className="h-[15px] w-[15px]" style={{ color: "var(--accent)" }} strokeWidth={1.75} />
            Copilot
          </button>

          <ThemeToggle />

          <Link
            href="/settings"
            title={user ? `${username} (${user.role})` : "Account"}
            className="inline-flex h-9 items-center gap-2 rounded-[var(--radius-control)] px-3 text-sm font-medium transition-colors hover:bg-[var(--canvas)]"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <span
              className="grid h-5 w-5 place-items-center rounded-full text-[10px] font-semibold"
              style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
            >
              {initial}
            </span>
            {username || "…"}
          </Link>
        </div>
      </div>

      <GlobalSearch />
    </header>
  );
}
