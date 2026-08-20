"use client";

import React from "react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail ?? "Invalid username or password.");
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
    <div className="flex min-h-screen items-center justify-center bg-canvas">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-[var(--radius-control)] p-6"
        style={{ border: "1px solid var(--border)", background: "var(--surface)" }}
      >
        <div className="eyebrow">Cortex</div>
        <h1 className="font-display mt-1 text-xl font-semibold text-color-text">Sign in</h1>

        <div className="mt-5 flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm text-text-dim">
            Username
            <input
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-2 text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-text-dim">
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-2 text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              required
            />
          </label>
        </div>

        {error && (
          <p className="mt-3 text-sm" style={{ color: "var(--crit)" }}>
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="mt-5 w-full rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold text-white disabled:opacity-60"
          style={{ background: "var(--accent, #4f46e5)" }}
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
