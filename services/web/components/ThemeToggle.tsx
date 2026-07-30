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
      className="p-2 rounded-md hover:bg-bg-hover"
    >
      {dark ? <Moon /> : <Sun />}
    </button>
  );
}
