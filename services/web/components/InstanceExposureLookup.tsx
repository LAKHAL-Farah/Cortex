"use client";

import { useState } from "react";
import { Boxes, Search, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { InstanceExposureResult } from "@/lib/types";
import { Card } from "./ui/Card";
import SecurityScopeTag from "./SecurityScopeTag";

/** Phase Sec-5b: "explicit stretch, not assumed" -- unlike Sec-5a's
 * fleet-wide table, this isn't part of any node's periodic scan
 * (services/instance_exposure.py has nothing to poll on a schedule, see
 * that module's own docstring), so this is a one-instance-at-a-time,
 * on-demand lookup rather than another row in ExposedPortsTable. Hits
 * GET /api/security/instance-exposed-ports/{instance_id} live, every
 * time -- a real TCP-connect probe against that instance's own floating/
 * fixed IP, not a cached read.
 */
export default function InstanceExposureLookup() {
  const [instanceId, setInstanceId] = useState("");
  const [result, setResult] = useState<InstanceExposureResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    const id = instanceId.trim();
    if (!id) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`/api/security/instance-exposed-ports/${encodeURIComponent(id)}`);
      if (!res.ok) throw new Error(`GET instance-exposed-ports -> ${res.status}`);
      setResult(await res.json());
    } catch {
      setError("Couldn't reach the instance-exposure check right now.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <Boxes className="h-4 w-4" style={{ color: "var(--chart-2)" }} strokeWidth={2} />
          <span className="font-display text-[14px] font-semibold text-color-text">Instance reachability</span>
          <SecurityScopeTag scope="instance" />
        </div>
        <p className="mt-1 text-sm text-text-faint">
          Sec-5a above reads what a <span className="font-medium text-text-dim">node</span> itself is listening on.
          A guest VM&apos;s own network stack isn&apos;t reachable that way at all -- this instead does a real
          TCP-connect probe against one instance&apos;s floating or fixed IP, on demand, checking whether its own
          declared world-open security-group rules are actually backed by something answering right now.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <input
          value={instanceId}
          onChange={(e) => setInstanceId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && check()}
          placeholder="Instance ID or name (e.g. sandbox-vm-1)"
          className="flex-1 rounded-[var(--radius-control)] border px-3 py-2 text-sm text-color-text"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        />
        <button
          onClick={check}
          disabled={loading || !instanceId.trim()}
          type="button"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          style={{ background: "var(--accent)" }}
        >
          <Search className="h-3.5 w-3.5" strokeWidth={2} />
          {loading ? "Checking…" : "Check"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm" style={{ color: "var(--crit)" }}>
          <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={2} />
          {error}
        </div>
      )}

      {result && <InstanceExposureOutcome result={result} />}
    </Card>
  );
}

function InstanceExposureOutcome({ result }: { result: InstanceExposureResult }) {
  if (result.restricted) {
    return (
      <div className="flex items-center gap-2 rounded-[var(--radius-control)] p-3 text-sm" style={{ background: "var(--canvas)" }}>
        <ShieldQuestion className="h-4 w-4 shrink-0 text-text-faint" strokeWidth={2} />
        <span className="text-text-faint">
          {result.has_signal ? "This instance has a confirmed reachability finding" : "No confirmed reachability finding"} --
          details restricted to admin accounts.
        </span>
      </div>
    );
  }

  if (result.degraded) {
    return (
      <div className="flex items-center gap-2 rounded-[var(--radius-control)] p-3 text-sm" style={{ background: "var(--warn-soft)", color: "var(--warn)" }}>
        <ShieldQuestion className="h-4 w-4 shrink-0" strokeWidth={2} />
        {result.detail ?? "Couldn't resolve this instance."}
      </div>
    );
  }

  if (result.has_signal) {
    return (
      <div className="space-y-3 rounded-[var(--radius-control)] p-3" style={{ background: "var(--crit-soft)" }}>
        <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--crit)" }}>
          <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={2} />
          {result.detail}
        </div>
        {result.reachable_ip && (
          <div className="text-xs text-text-faint">
            {result.instance_name ?? result.instance_id} · reachable at{" "}
            <span className="font-mono text-text-dim">{result.reachable_ip}</span> ({result.reachable_via === "floating_ip" ? "floating IP" : "fixed IP"})
          </div>
        )}
        {(result.confirmed?.length ?? 0) > 0 && (
          <ul className="space-y-1 text-sm text-text-dim">
            {result.confirmed!.map((p) => (
              <li key={p.port} className="flex items-center gap-2">
                <span className="font-mono">{p.port}</span>
                <span className="text-text-faint">{p.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2 rounded-[var(--radius-control)] p-3 text-sm" style={{ background: "var(--ok-soft)", color: "var(--ok)" }}>
      <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2} />
      <div>
        <div>{result.detail}</div>
        {(result.unconfirmed?.length ?? 0) > 0 && (
          <div className="mt-1 text-xs" style={{ color: "var(--text-faint)" }}>
            {result.unconfirmed!.length} declared-open port(s) didn&apos;t answer when probed -- unconfirmed, not reachable right now.
          </div>
        )}
      </div>
    </div>
  );
}
