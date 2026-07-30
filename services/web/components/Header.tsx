"use client";

import React from "react";
import { Search, Bell, User, RefreshCw, Zap } from "lucide-react";
import ThemeToggle from "./ThemeToggle";

export default function Header() {
  const [q, setQ] = React.useState("");

  const doRefresh = () => {
    window.dispatchEvent(new Event("cortex:refresh"));
  };

  const openChat = () => {
    window.location.href = "/copilot";
  };

  return (
    <header className="rounded-[20px] border border-[#ECECEC] bg-white p-6 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-2xl">
          <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Workspace</div>
          <h1 className="mt-3 text-3xl font-semibold text-color-text">Welcome back, operator.</h1>
          <p className="mt-3 text-sm leading-6 text-text-dim">Track infrastructure health, performance, and capacity from a single command center.</p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={doRefresh}
            title="Refresh data"
            className="inline-flex h-11 items-center justify-center rounded-[12px] border border-[#ECECEC] bg-[#F8FAFC] px-4 text-sm font-medium text-color-text transition hover:bg-[#f4f5f7]"
            aria-label="Refresh"
          >
            <RefreshCw className="h-4 w-4" />
          </button>

          <button
            title="Notifications"
            className="relative inline-flex h-11 items-center justify-center rounded-[12px] border border-[#ECECEC] bg-[#F8FAFC] px-4 text-sm font-medium text-color-text transition hover:bg-[#f4f5f7]"
          >
            <Bell className="h-4 w-4" />
            <span className="absolute -right-2 -top-2 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-red-600 px-1.5 text-[10px] font-semibold text-white">3</span>
          </button>

          <button
            onClick={openChat}
            title="Open AI Copilot"
            className="inline-flex items-center gap-2 rounded-[12px] border border-[#ECECEC] bg-[#F8FAFC] px-4 py-3 text-sm font-medium text-color-text transition hover:bg-[#f4f5f7]"
          >
            <Zap className="h-4 w-4 text-orange-600" />
            <span>Copilot</span>
          </button>

          <ThemeToggle />

          <button className="inline-flex items-center gap-2 rounded-[12px] border border-[#ECECEC] bg-[#F8FAFC] px-4 py-3 text-sm font-medium text-color-text transition hover:bg-[#f4f5f7]">
            <User className="h-4 w-4" />
            Alex
          </button>
        </div>
      </div>

      <div className="mt-6 rounded-[14px] border border-[#ECECEC] bg-[#F8FAFC] p-3">
        <div className="relative flex items-center">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search the cockpit..."
            className="w-full rounded-[12px] border border-transparent bg-white px-4 py-3 text-sm text-color-text outline-none transition focus:border-orange-300 focus:ring-2 focus:ring-orange-100"
            aria-label="Global search"
          />
          <Search className="pointer-events-none absolute right-4 h-4 w-4 text-text-faint" />
        </div>
      </div>
    </header>
  );
}
