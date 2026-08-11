"use client";
import React from "react";
import { Sun, Moon } from "lucide-react";

export default function ThemeToggle() {
  const [dark, setDark] = React.useState(() => {
    try {
      const t = localStorage.getItem("cortex-theme");
      if (t) return t === "dark";
      return matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e) {
      return false;
    }
  });

  React.useEffect(() => {
    try {
      const doc = document.documentElement;
      if (dark) doc.classList.add("dark");
      else doc.classList.remove("dark");
      localStorage.setItem("cortex-theme", dark ? "dark" : "light");
    } catch (e) {}
  }, [dark]);

  return (
    <button
      onClick={() => setDark((d) => !d)}
      aria-label="Toggle theme"
      title="Toggle theme"
      className="inline-flex h-9 w-9 items-center justify-center rounded-[var(--radius-control)] text-text-dim transition-colors hover:bg-[var(--canvas)]"
      style={{ border: "1px solid var(--border)" }}
    >
      {dark ? <Moon className="h-[15px] w-[15px]" strokeWidth={1.75} /> : <Sun className="h-[15px] w-[15px]" strokeWidth={1.75} />}
    </button>
  );
}
