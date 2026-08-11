"use client";
import { useState } from "react";
import { mutate } from "swr";
import { Plus, X } from "lucide-react";
import type { NodeRole } from "@/lib/types";

const ROLES: NodeRole[] = ["controller", "compute", "storage", "monitoring"];

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="eyebrow mb-1.5 block">{label}</label>
      {children}
    </div>
  );
}

const inputClass =
  "w-full rounded-[var(--radius-control)] px-3.5 py-2.5 text-sm text-color-text outline-none transition-colors";
const inputStyle = { border: "1px solid var(--border)", background: "var(--canvas)" } as const;

export default function NodeForm() {
  const [open, setOpen] = useState(false);
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
      mutate("/api/nodes");
      mutate("/api/dashboard");
      setOpen(false);
    } else {
      setError(`Error ${res.status}: ${JSON.stringify(payload.detail ?? payload)}`);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
        style={{ background: "var(--accent)" }}
      >
        <Plus className="h-4 w-4" strokeWidth={2} />
        Add node
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(15,17,20,0.5)" }}
          onClick={() => setOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            className="panel w-full max-w-md p-5"
            style={{ boxShadow: "var(--shadow-hover)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <div>
                <div className="eyebrow">Infrastructure</div>
                <h2 className="font-display mt-1 text-lg font-semibold text-color-text">Add node</h2>
              </div>
              <button
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-control)] text-text-faint transition-colors hover:bg-[var(--canvas)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={onSubmit} className="grid gap-3.5">
              <Field label="Hostname">
                <input name="hostname" required placeholder="compute-04" className={inputClass} style={inputStyle} />
              </Field>
              <Field label="IP address">
                <input name="ip_address" required placeholder="10.0.1.24" className={inputClass} style={inputStyle} />
              </Field>
              <div className="grid grid-cols-2 gap-3.5">
                <Field label="Role">
                  <select name="role" defaultValue="compute" className={inputClass} style={inputStyle}>
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Exporter port">
                  <input name="exporter_port" type="number" defaultValue={9100} className={inputClass} style={inputStyle} />
                </Field>
              </div>

              {error && <p className="text-sm" style={{ color: "var(--crit)" }}>{error}</p>}

              <div className="mt-1 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-medium text-text-dim transition-colors hover:bg-[var(--canvas)]"
                  style={{ border: "1px solid var(--border)" }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={pending}
                  className="inline-flex items-center justify-center rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ background: "var(--accent)" }}
                >
                  {pending ? "Adding…" : "Add node"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
