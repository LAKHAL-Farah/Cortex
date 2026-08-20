"use client";

import React from "react";
import { Card } from "@/components/ui/Card";
import { useCurrentUser } from "@/lib/useCurrentUser";

type AdminUser = {
  id: string;
  username: string;
  role: "admin" | "viewer";
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
};

export default function AdminUsersPage() {
  const { user: me, loading: meLoading } = useCurrentUser();
  const [users, setUsers] = React.useState<AdminUser[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const [newUsername, setNewUsername] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [newRole, setNewRole] = React.useState<"admin" | "viewer">("viewer");
  const [creating, setCreating] = React.useState(false);
  const [createdInfo, setCreatedInfo] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/users", { cache: "no-store" });
      if (!res.ok) throw new Error("Failed to load users");
      setUsers(await res.json());
    } catch {
      setError("Couldn't load users.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const createUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setError(null);
    setCreatedInfo(null);
    try {
      const res = await fetch("/api/auth/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: newUsername, password: newPassword, role: newRole }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail ?? "Couldn't create the user.");
        return;
      }
      setCreatedInfo(
        `Created "${newUsername}". Share this temporary password with them -- they'll be asked to change it on first login.`
      );
      setNewUsername("");
      setNewPassword("");
      setNewRole("viewer");
      load();
    } finally {
      setCreating(false);
    }
  };

  const patchUser = async (id: string, body: Record<string, unknown>) => {
    setError(null);
    const res = await fetch(`/api/auth/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setError(data.detail ?? "That change was rejected.");
      return;
    }
    load();
  };

  const resetPassword = (u: AdminUser) => {
    const pw = window.prompt(`New temporary password for "${u.username}" (min 8 characters):`);
    if (!pw) return;
    if (pw.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    patchUser(u.id, { new_password: pw });
  };

  if (!meLoading && me && me.role !== "admin") {
    return (
      <Card>
        <p className="text-sm text-text-dim">You need admin privileges to view this page.</p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="eyebrow">Administration</div>
        <h1 className="font-display mt-1 text-xl font-semibold text-color-text">Users</h1>
      </div>

      <Card>
        <h2 className="text-sm font-semibold text-color-text">Add a user</h2>
        <form onSubmit={createUser} className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Username
            <input
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-1.5 text-sm text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Temporary password
            <input
              type="text"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="rounded-[var(--radius-control)] px-3 py-1.5 text-sm text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
              minLength={8}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-dim">
            Role
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as "admin" | "viewer")}
              className="rounded-[var(--radius-control)] px-3 py-1.5 text-sm text-color-text"
              style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
            >
              <option value="viewer">Viewer</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={creating}
            className="rounded-[var(--radius-control)] px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-60"
            style={{ background: "var(--accent, #4f46e5)" }}
          >
            {creating ? "Creating..." : "Create user"}
          </button>
        </form>
        {createdInfo && <p className="mt-2 text-sm" style={{ color: "var(--ok, #16a34a)" }}>{createdInfo}</p>}
      </Card>

      {error && (
        <p className="text-sm" style={{ color: "var(--crit)" }}>
          {error}
        </p>
      )}

      <Card padding="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-text-dim" style={{ borderBottom: "1px solid var(--border)" }}>
              <th className="px-4 py-3 font-medium">Username</th>
              <th className="px-4 py-3 font-medium">Role</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Password</th>
              <th className="px-4 py-3 font-medium text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-text-dim">
                  Loading...
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr key={u.id} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                  <td className="px-4 py-2.5 text-color-text">{u.username}</td>
                  <td className="px-4 py-2.5">
                    <select
                      value={u.role}
                      disabled={u.id === me?.id}
                      onChange={(e) => patchUser(u.id, { role: e.target.value })}
                      className="rounded-[var(--radius-control)] px-2 py-1 text-xs text-color-text"
                      style={{ border: "1px solid var(--border)", background: "var(--canvas)" }}
                    >
                      <option value="viewer">Viewer</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td className="px-4 py-2.5">
                    <button
                      disabled={u.id === me?.id}
                      onClick={() => patchUser(u.id, { is_active: !u.is_active })}
                      className="rounded-full px-2 py-0.5 text-xs font-medium disabled:opacity-60"
                      style={{
                        background: u.is_active ? "var(--ok-soft, #dcfce7)" : "var(--crit-soft, #fee2e2)",
                        color: u.is_active ? "var(--ok, #16a34a)" : "var(--crit)",
                      }}
                    >
                      {u.is_active ? "Active" : "Disabled"}
                    </button>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-text-dim">
                    {u.must_change_password ? "Must change on next login" : "Set"}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => resetPassword(u)}
                      className="text-xs font-medium"
                      style={{ color: "var(--accent, #4f46e5)" }}
                    >
                      Reset password
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
