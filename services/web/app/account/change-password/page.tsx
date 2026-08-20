"use client";

import React from "react";
import { useRouter, useSearchParams } from "next/navigation";

function ChangePasswordForm() {
  const router = useRouter();
  const forced = useSearchParams().get("forced") === "1";

  const [currentPassword, setCurrentPassword] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail ?? "Couldn't change the password.");
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
        <h1 className="font-display mt-1 text-xl font-semibold text-color-text">
          {forced ? "Set a new password" : "Change password"}
        </h1>
        {forced && (
          <p className="mt-1 text-sm text-text-dim">
            You&rsquo;re using a temporary password. Choose a new one to continue.
          </p>
        )}

        <div className="mt-5 flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm text-text-dim">
            Current password
            <input
              type="password"
              autoFocus
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-2 text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-text-dim">
            New password
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-2 text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              minLength={8}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-text-dim">
            Confirm new password
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-2 text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              minLength={8}
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
          {loading ? "Saving..." : "Save new password"}
        </button>
      </form>
    </div>
  );
}

export default function ChangePasswordPage() {
  return (
    <React.Suspense fallback={null}>
      <ChangePasswordForm />
    </React.Suspense>
  );
}
