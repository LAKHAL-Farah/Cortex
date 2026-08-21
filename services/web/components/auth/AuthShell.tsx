"use client";

import React from "react";
import { motion } from "framer-motion";
import ThemeToggle from "@/components/ThemeToggle";

export function AuthShell({
  eyebrow,
  title,
  subtitle,
  children,
  footer,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1fr_minmax(460px,560px)]">
      {/* brand column — just the photo. Drop your image at
          services/web/public/auth/hero.jpg (or change HERO_IMAGE_SRC below)
          and it fills this panel edge-to-edge. Until that file exists this
          is blank/var(--canvas) — no gradient, no accent shapes standing in
          for it. */}
      <div
        className="relative hidden lg:block"
        style={{
          borderRight: "1px solid var(--border)",
          backgroundImage: `url(${HERO_IMAGE_SRC})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
          backgroundColor: "var(--canvas)",
        }}
      />

      {/* form column */}
      <div className="relative flex flex-col justify-center px-6 py-12 sm:px-16" style={{ background: "var(--surface)" }}>
        <div className="absolute right-5 top-5">
          <ThemeToggle />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18 }}
          className="mx-auto w-full max-w-md"
        >
          <div className="mb-8 flex items-center gap-2">
            <span
              className="grid h-7 w-7 place-items-center rounded-[var(--radius-control)] text-sm font-semibold text-white"
              style={{ background: "var(--accent)" }}
            >
              C
            </span>
            <span className="font-display text-[15px] font-semibold text-color-text">Cortex</span>
          </div>

          <div className="eyebrow">{eyebrow}</div>
          <h1 className="font-display mt-1 text-2xl font-semibold text-color-text">{title}</h1>
          {subtitle && <p className="mt-2 text-sm text-text-dim">{subtitle}</p>}

          <div className="mt-6">{children}</div>

          {footer && <div className="mt-6 text-sm text-text-faint">{footer}</div>}
        </motion.div>
      </div>
    </div>
  );
}

// Swap this for wherever you keep the real product photo. Anything placed
// at services/web/public/auth/hero.png is served at this path automatically.
const HERO_IMAGE_SRC = "/auth/hero.png";

export default AuthShell;
