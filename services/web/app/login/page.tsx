"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { User, Lock, AlertCircle } from "lucide-react";
import { AuthShell } from "@/components/auth/AuthShell";
import { AuthField } from "@/components/auth/AuthField";

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
    <AuthShell
      eyebrow="Welcome back"
      title="Sign in"
      subtitle="Use your workspace credentials to access the cockpit."
      footer={<span>Need access? Ask your workspace admin to invite you.</span>}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <AuthField id="username" label="Username" icon={User} value={username} onChange={setUsername} autoFocus required />
        <AuthField id="password" label="Password" icon={Lock} showToggle value={password} onChange={setPassword} required />

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
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </AuthShell>
  );
}
