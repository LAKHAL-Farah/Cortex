"use client";

import React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Lock, AlertCircle } from "lucide-react";
import { AuthShell } from "@/components/auth/AuthShell";
import { AuthField } from "@/components/auth/AuthField";

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
    <AuthShell
      eyebrow="Cortex"
      title={forced ? "Set a new password" : "Change password"}
      subtitle={forced ? "You're using a temporary password. Choose a new one to continue." : undefined}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <AuthField
          id="current-password"
          label="Current password"
          icon={Lock}
          showToggle
          value={currentPassword}
          onChange={setCurrentPassword}
          autoFocus
          required
        />
        <AuthField
          id="new-password"
          label="New password"
          icon={Lock}
          showToggle
          value={newPassword}
          onChange={setNewPassword}
          minLength={8}
          required
        />
        <AuthField
          id="confirm-password"
          label="Confirm new password"
          icon={Lock}
          showToggle
          value={confirm}
          onChange={setConfirm}
          minLength={8}
          required
        />

        {error && (
          <p className="flex items-center gap-1.5 text-sm" style={{ color: "var(--crit)" }}>
            <AlertCircle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="mt-1 inline-flex h-10 items-center justify-center rounded-[var(--radius-control)] text-sm font-semibold text-white transition-opacity disabled:opacity-60"
          style={{ background: "var(--accent)" }}
        >
          {loading ? "Saving…" : "Save new password"}
        </button>
      </form>
    </AuthShell>
  );
}

export default function ChangePasswordPage() {
  return (
    <React.Suspense fallback={null}>
      <ChangePasswordForm />
    </React.Suspense>
  );
}
