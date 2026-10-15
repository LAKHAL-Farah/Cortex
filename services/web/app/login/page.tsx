"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import Link from "next/link";
import { Logo } from "@/components/Logo";
import ThemeToggle from "@/components/ThemeToggle";
import styles from "../public-pages.module.css";
import { apiErrorMessage } from "@/lib/apiError";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        setError(apiErrorMessage(data.detail, "Invalid username or password."));
        return;
      }

      if (data.user?.must_change_password) {
        router.push("/account/change-password?forced=1");
        return;
      }

      router.push("/dashboard");
      router.refresh();
    } catch {
      setError("Couldn't reach the server. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className={`${styles.login} relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10`}>
      <div aria-hidden="true" className={styles.loginDecor}>
        {[
          ["left-[8%] top-[14%] h-16 w-16 rounded-full", 14],
          ["right-[12%] top-[20%] h-12 w-12 rotate-12 rounded-lg", -10],
          ["bottom-[15%] left-[15%] h-10 w-10 rounded-full", 12],
          ["bottom-[25%] right-[8%] h-14 w-14 -rotate-12 rounded-lg", -16],
          ["left-[5%] top-[50%] h-8 w-8 rounded-full", 8],
          ["right-[5%] top-[40%] h-20 w-20 rounded-full", -20],
        ].map(([shape, movement], index) => (
          <motion.div
            key={shape as string}
            className={`${styles.loginDecorShape} ${shape}`}
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1, y: [0, movement as number, 0], x: [0, (movement as number) / 2, 0] }}
            transition={{
              opacity: { duration: 0.8, delay: index * 0.08 },
              scale: { duration: 0.8, delay: index * 0.08 },
              y: { duration: 7 + index, repeat: Infinity, ease: "easeInOut", delay: index * 0.2 },
              x: { duration: 9 + index, repeat: Infinity, ease: "easeInOut", delay: index * 0.2 },
            }}
          />
        ))}
      </div>

      <div className="absolute right-5 top-5">
        <ThemeToggle />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="relative z-10 w-full max-w-md"
      >
        <div className={styles.loginBrand}>
          <Link href="/" aria-label="Cortex home" className="inline-block transition-transform hover:scale-[1.03]">
            <Logo size="lg" />
          </Link>
          <p className={styles.loginTagline}>Create events people actually want to attend</p>
        </div>

        <div className={styles.loginCard}>
          <form onSubmit={submit} className={styles.loginForm} data-testid="login-form">
            <label className={styles.loginField}>
              <span className={styles.loginLabel}>Username</span>
              <input
                autoFocus
                required
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                type="text"
                placeholder="Enter your username"
                className={styles.loginInput}
                autoComplete="username"
                data-testid="login-username"
              />
            </label>

            <label className={styles.loginField}>
              <span className={styles.loginLabel}>Password</span>
              <input
                required
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••"
                className={styles.loginInput}
                autoComplete="current-password"
                data-testid="login-password"
              />
            </label>

            {error && (
              <p role="alert" className={styles.loginError}>{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className={styles.loginSubmit}
              data-testid="login-submit"
            >
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </form>

          <p className={styles.loginFooter}>
            Need access? Ask your workspace admin to invite you.
          </p>
        </div>
      </motion.div>
    </main>
  );
}
