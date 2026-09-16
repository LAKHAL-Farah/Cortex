"use client";

import { useMemo } from "react";
import useSWR from "swr";
import { Boxes, RefreshCw, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { InstanceExposureResult } from "@/lib/types";
import { Card } from "./ui/Card";
import SecurityScopeTag from "./SecurityScopeTag";

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
};

type RowTone = "confirmed" | "unconfirmed" | "clean" | "degraded" | "restricted";

function toneFor(row: InstanceExposureResult): RowTone {
  if (row.restricted) return "restricted";
  if (row.degraded) return "degraded";
  if (row.has_signal) return "confirmed";
  if ((row.unconfirmed?.length ?? 0) > 0) return "unconfirmed";
  return "clean";
}

const TONE_STYLE: Record<RowTone, { bg: string; fg: string; Icon: typeof ShieldCheck; label: string }> = {
  confirmed: { bg: "var(--crit-soft)", fg: "var(--crit)", Icon: ShieldAlert, label: "Confirmed reachable" },
  unconfirmed: { bg: "var(--warn-soft)", fg: "var(--warn)", Icon: ShieldQuestion, label: "Unconfirmed" },
  clean: { bg: "var(--ok-soft)", fg: "var(--ok)", Icon: ShieldCheck, label: "Clean" },
  degraded: { bg: "var(--warn-soft)", fg: "var(--warn)", Icon: ShieldQuestion, label: "Unknown" },
  restricted: { bg: "var(--canvas)", fg: "var(--text-faint)", Icon: ShieldQuestion, label: "Admin only" },
};

/** Phase Sec-5b, fleet-wide table variant. Replaces the earlier one-
 * instance-at-a-time InstanceExposureLookup with a row per instance the
 * topology graph knows about (GET /api/v1/security/instance-exposed-
 * ports, no path param -- see that router handler's own docstring),
 * matching ExposedPortsTable's shape above it on this same page. Still
 * genuinely live on every load, not cached: each row is a real TCP-
 * connect probe against that instance's own floating/fixed IP, run
 * concurrently across instances server-side, not a periodic scan result
 * -- see services/instance_exposure.py's module docstring for why this
 * check has no scan loop to ride the way Sec-5a's node table does. A
 * longer refresh interval and no revalidate-on-focus reflect that cost:
 * unlike the findings-cache-backed table above, refetching this one
 * isn't free.
 */
export default function InstanceExposureTable() {
  const { data, error, isLoading, mutate } = useSWR<{ instances: InstanceExposureResult[] }>(
    "/api/security/instance-exposed-ports",
    fetcher,
    { refreshInterval: 60000, revalidateOnFocus: false },
  );

  const rows = useMemo(() => data?.instances ?? [], [data]);
  const confirmedCount = rows.filter((r) => !r.restricted && !r.degraded && r.has_signal).length;

  return (
    <Card className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Boxes className="h-4 w-4" style={{ color: "var(--chart-2)" }} strokeWidth={2} />
            <span className="font-display text-[14px] font-semibold text-color-text">Instance reachability</span>
            <SecurityScopeTag scope="instance" />
          </div>
          <p className="mt-1 text-sm text-text-faint">
            Sec-5a above reads what a <span className="font-medium text-text-dim">node</span> itself is listening on.
            A guest VM&apos;s own network stack isn&apos;t reachable that way at all -- every instance below gets a
            real TCP-connect probe against its own floating or fixed IP, checking whether its declared world-open
            security-group rules are actually backed by something answering right now.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          disabled={isLoading}
          type="button"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-[var(--radius-control)] px-2.5 py-1.5 text-xs font-medium text-text-dim disabled:opacity-50"
          style={{ border: "1px solid var(--border)" }}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} strokeWidth={2} />
          Rescan
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm" style={{ color: "var(--crit)" }}>
          <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={2} />
          Couldn&apos;t reach the instance-exposure check right now.
        </div>
      )}

      {isLoading && !data ? (
        <div className="flex items-center gap-2 text-sm text-text-faint">
          <RefreshCw className="h-4 w-4 animate-spin" strokeWidth={2} />
          Probing every known instance…
        </div>
      ) : rows.length === 0 ? (
        <div className="text-sm text-text-faint">No instances found in topology yet.</div>
      ) : (
        <div className="overflow-hidden rounded-[var(--radius-control)]" style={{ border: "1px solid var(--border-soft)" }}>
          <div
            className="hidden grid-cols-[1.3fr_1.1fr_0.9fr_2fr] gap-4 px-4 py-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted sm:grid"
            style={{ background: "var(--canvas)" }}
          >
            <div>Instance</div>
            <div>Reachable IP</div>
            <div>Status</div>
            <div>Detail</div>
          </div>
          <div>
            {rows.map((row, idx) => {
              const tone = toneFor(row);
              const style = TONE_STYLE[tone];
              return (
                <div
                  key={row.instance_id ?? idx}
                  className="grid w-full gap-2 border-b p-4 text-left last:border-b-0 sm:grid-cols-[1.3fr_1.1fr_0.9fr_2fr] sm:items-center sm:gap-4"
                  style={{ borderColor: "var(--border-soft)" }}
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-color-text">
                      {row.instance_name ?? row.instance_id ?? "unknown instance"}
                    </div>
                    {row.instance_name && row.instance_id && (
                      <div className="truncate font-mono text-xs text-text-faint">{row.instance_id}</div>
                    )}
                  </div>

                  <div className="text-sm text-text-dim">
                    {row.reachable_ip ? (
                      <>
                        <span className="font-mono">{row.reachable_ip}</span>{" "}
                        <span className="text-xs text-text-faint">
                          ({row.reachable_via === "floating_ip" ? "floating" : "fixed"})
                        </span>
                      </>
                    ) : (
                      <span className="text-text-faint">none found</span>
                    )}
                  </div>

                  <div>
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
                      style={{ background: style.bg, color: style.fg }}
                    >
                      <style.Icon className="h-3 w-3" strokeWidth={2} />
                      {style.label}
                    </span>
                  </div>

                  <div className="text-sm text-text-faint">
                    <p className="line-clamp-2">{row.detail}</p>
                    {tone === "confirmed" && (row.confirmed?.length ?? 0) > 0 && (
                      <p className="mt-0.5 font-mono text-xs text-text-dim">
                        {row.confirmed!.map((p) => p.port).join(", ")}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!isLoading && rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 px-1 text-xs text-text-faint">
          <ShieldCheck className="h-3.5 w-3.5" style={{ color: "var(--ok)" }} strokeWidth={2} />
          <span>
            {confirmedCount === 0
              ? "No confirmed reachable instances this pass."
              : `${confirmedCount} instance${confirmedCount === 1 ? "" : "s"} confirmed reachable.`}
          </span>
        </div>
      )}
    </Card>
  );
}
