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
    <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-5 items-end">
      <div className="sm:col-span-1">
        <label className="block text-sm mb-1">Hostname</label>
        <input name="hostname" required className="w-full rounded border px-2 py-1" />
      </div>
      <div>
        <label className="block text-sm mb-1">IP address</label>
        <input name="ip_address" required className="w-full rounded border px-2 py-1" />
      </div>
      <div>
        <label className="block text-sm mb-1">Role</label>
        <select name="role" defaultValue="compute" className="w-full rounded border px-2 py-1">
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-sm mb-1">Exporter port</label>
        <input name="exporter_port" type="number" defaultValue={9100}
               className="w-full rounded border px-2 py-1" />
      </div>
      <button disabled={pending} className="rounded bg-black text-white px-4 py-1.5 disabled:opacity-50">
        {pending ? "Adding…" : "Add node"}
      </button>
      {error && <p className="sm:col-span-5 text-sm text-red-600">{error}</p>}
    </form>
  );
}