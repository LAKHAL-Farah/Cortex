"use client";
import { useState } from "react";
import { mutate } from "swr";
import type { NodeRole } from "@/lib/types";

const ROLES: NodeRole[] = ["controller", "compute", "storage", "monitoring"];

export default function NodeForm() {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setPending(true);
    const fd = new FormData(e.currentTarget);
    const body = {
      hostname: fd.get("hostname"),
      ip_address: fd.get("ip_address"),
      role: fd.get("role"),
      exporter_port: Number(fd.get("exporter_port")),
      is_active: true,
    };
    const res = await fetch("/api/nodes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json().catch(() => ({}));
    setPending(false);
    if (res.ok) {
      e.currentTarget.reset();
      mutate("/api/nodes");       // triggers instant revalidation, no manual reload
    } else {
      setError(`Error ${res.status}: ${JSON.stringify(payload.detail ?? payload)}`);
    }
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-4 rounded-[28px] border border-color-border bg-bg-sunk p-5 shadow-sm sm:grid-cols-5">
      <div className="sm:col-span-1">
        <label className="block text-xs uppercase tracking-[0.24em] text-text-faint mb-2">Hostname</label>
        <input name="hostname" required className="w-full rounded-3xl border border-color-border bg-bg px-4 py-3 text-sm text-color-text outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
      </div>
      <div>
        <label className="block text-xs uppercase tracking-[0.24em] text-text-faint mb-2">IP address</label>
        <input name="ip_address" required className="w-full rounded-3xl border border-color-border bg-bg px-4 py-3 text-sm text-color-text outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
      </div>
      <div>
        <label className="block text-xs uppercase tracking-[0.24em] text-text-faint mb-2">Role</label>
        <select name="role" defaultValue="compute" className="w-full rounded-3xl border border-color-border bg-bg px-4 py-3 text-sm text-color-text outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100">
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-xs uppercase tracking-[0.24em] text-text-faint mb-2">Exporter port</label>
        <input name="exporter_port" type="number" defaultValue={9100} className="w-full rounded-3xl border border-color-border bg-bg px-4 py-3 text-sm text-color-text outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100" />
      </div>
      <div className="flex items-center justify-end sm:justify-start">
        <button type="submit" disabled={pending} className="inline-flex h-12 items-center justify-center rounded-3xl bg-blue text-white px-6 text-sm font-semibold transition hover:bg-blue-600 disabled:cursor-not-allowed disabled:opacity-60">
          {pending ? "Adding…" : "Add node"}
        </button>
      </div>
      {error && <p className="sm:col-span-5 text-sm text-red-600">{error}</p>}
    </form>
  );
}