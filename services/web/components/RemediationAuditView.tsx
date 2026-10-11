"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  ChevronRight,
  Circle,
  ClipboardCheck,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { formatRelative } from "@/lib/anomalies";
import type {
  IncidentAlert,
  IncidentAudit,
  IncidentAuditEntry,
  IncidentStage,
  IncidentSummary,
} from "@/lib/types";

/** Roadmap 4.8 -- "audit log queryable end to end for one full incident".
 *
 * Left: recent incidents (one per agent run that produced a fix proposal).
 * Right: the whole story for the selected one -- diagnose -> propose ->
 * validate -> execute, who did each step and why, the alerts that were open on
 * that host meanwhile, and whether the tamper-evident hash chain still
 * verifies. Admins and operators only; the API enforces it. */

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new HttpError(res.status, data?.detail ?? `Request failed (${res.status})`);
  return data;
};

const STAGE_LABEL: Record<string, string> = {
  diagnose: "Diagnose",
  propose: "Propose",
  validate: "Validate",
  execute: "Execute",
};

const EVENT_LABEL: Record<string, string> = {
  diagnosed: "Diagnosed by the agent",
  proposed: "Fix proposed",
  approved: "Approved",
  rejected: "Rejected",
  info_requested: "Asked for more information",
  info_provided: "Answer provided",
  execution_started: "Execution started",
  executed: "Executed",
  execution_failed: "Execution failed",
};

const OUTCOME_LABEL: Record<string, string> = {
  executed: "Executed",
  executing: "Running",
  execution_failed: "Failed",
  approved: "Approved, not run",
  rejected: "Rejected",
  info_requested: "Question open",
  proposed: "Awaiting decision",
  no_proposal: "No proposal",
};

function outcomeColor(outcome: string): string {
  if (outcome === "executed") return "var(--ok)";
  if (outcome === "execution_failed" || outcome === "rejected") return "var(--crit)";
  if (outcome === "approved" || outcome === "executing") return "var(--accent)";
  return "var(--warn)";
}

function riskColor(risk: string | null): string {
  if (risk === "high") return "var(--crit)";
  if (risk === "medium") return "var(--warn)";
  if (risk === "low") return "var(--ok)";
  return "var(--text-muted)";
}

function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function Pill({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ color, background: "var(--canvas)", border: `1px solid ${color}` }}
    >
      {children}
    </span>
  );
}

function ChainBadge({ chain }: { chain: IncidentAudit["chain"] }) {
  return chain.ok ? (
    <span className="inline-flex items-center gap-1.5 text-xs" style={{ color: "var(--ok)" }}>
      <ShieldCheck className="h-4 w-4" />
      Chain intact · {chain.entries_checked} entries verified
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium" style={{ color: "var(--crit)" }}>
      <ShieldAlert className="h-4 w-4" />
      Chain broken at entry {chain.broken_at}: {chain.reason}
    </span>
  );
}

function StageStepper({ stages }: { stages: IncidentStage[] }) {
  return (
    <ol className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {stages.map((s) => (
        <li
          key={s.stage}
          className="rounded-[var(--radius-control)] p-3"
          style={{ background: "var(--canvas)", border: `1px solid ${s.done ? "var(--ok)" : "var(--border)"}` }}
        >
          <div className="flex items-center gap-1.5 text-sm font-medium text-color-text">
            {s.done ? (
              <CheckCircle2 className="h-4 w-4" style={{ color: "var(--ok)" }} />
            ) : (
              <Circle className="h-4 w-4 text-text-faint" />
            )}
            {STAGE_LABEL[s.stage]}
          </div>
          {s.done ? (
            <div className="mt-1 text-xs text-text-dim">
              {s.by ? <>by <span className="text-color-text">{s.by}</span> · </> : null}
              {when(s.at)}
              {!s.chained && <div className="mt-0.5 text-text-faint">not recorded in the chain</div>}
            </div>
          ) : (
            <div className="mt-1 text-xs text-text-faint">not reached</div>
          )}
        </li>
      ))}
    </ol>
  );
}

function EntryRow({ entry }: { entry: IncidentAuditEntry }) {
  const command = typeof entry.details?.command === "string" ? (entry.details.command as string) : null;
  return (
    <li className="relative border-l pl-4" style={{ borderColor: "var(--border)" }}>
      <span
        className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full"
        style={{ background: "var(--accent)" }}
      />
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-sm font-medium text-color-text">{EVENT_LABEL[entry.event] ?? entry.event}</span>
        <span className="text-xs text-text-dim">
          {entry.actor_username ?? "system"}
          {entry.actor_role ? ` (${entry.actor_role})` : ""} · {when(entry.created_at)}
        </span>
        <span className="text-[11px] text-text-faint">#{entry.id}</span>
      </div>
      {entry.comment && (
        <p className="mt-1 rounded-[var(--radius-control)] px-2.5 py-1.5 text-sm text-color-text" style={{ background: "var(--canvas)" }}>
          “{entry.comment}”
        </p>
      )}
      {command && <code className="mt-1 block break-all text-xs text-text-dim">{command}</code>}
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] text-text-faint">details · hash {entry.entry_hash.slice(0, 12)}…</summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded-[var(--radius-control)] p-2 text-[11px] text-text-dim" style={{ background: "var(--canvas)" }}>
          {JSON.stringify(entry.details, null, 2)}
        </pre>
      </details>
    </li>
  );
}

function AlertRow({ alert }: { alert: IncidentAlert }) {
  const r = alert.routing;
  const routed = !r ? "raised before routing existed"
    : !r.channels.includes("email") ? "dashboard only"
    : r.email_status === "sent" ? "emailed" : `email ${r.email_status ?? "queued"}`;
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <Bell className="h-3.5 w-3.5 text-text-faint" />
      <span className="text-color-text">{alert.hostname} · {alert.metric_name}</span>
      <Pill color={alert.severity === "critical" ? "var(--crit)" : alert.severity === "high" ? "var(--warn)" : "var(--text-muted)"}>
        {alert.severity}
      </Pill>
      <span className="text-xs text-text-dim">
        {routed}
        {r?.escalated ? " · escalated" : ""}
        {alert.acknowledged_by ? ` · acknowledged by ${alert.acknowledged_by}` : ""}
        {` · opened ${when(alert.started_at)}`}
      </span>
    </li>
  );
}

function IncidentDetail({ traceId }: { traceId: string }) {
  const { data, error, isLoading } = useSWR<IncidentAudit>(
    `/api/remediation/audit/incidents/${encodeURIComponent(traceId)}`, fetcher,
  );

  if (isLoading) return <div className="panel p-5 text-sm text-text-faint">Loading incident…</div>;
  if (error || !data) {
    return <div className="panel p-5 text-sm" style={{ color: "var(--crit)" }}>{error?.message ?? "Could not load this incident."}</div>;
  }

  return (
    <div className="grid gap-4">
      <section className="panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="eyebrow">Incident</div>
            <div className="mt-1 flex items-center gap-2">
              <h2 className="font-display text-base font-semibold text-color-text">
                {data.proposals.map((p) => p.host).filter(Boolean).join(", ") || "Unknown host"}
              </h2>
              <Pill color={outcomeColor(data.outcome)}>{OUTCOME_LABEL[data.outcome] ?? data.outcome}</Pill>
              {data.complete && <Pill color="var(--ok)">complete</Pill>}
            </div>
            <div className="mt-0.5 break-all text-[11px] text-text-faint">trace {data.trace_id}</div>
          </div>
          <ChainBadge chain={data.chain} />
        </div>
        <div className="mt-4"><StageStepper stages={data.stages} /></div>
      </section>

      <section className="panel p-5">
        <div className="eyebrow">Timeline</div>
        <p className="mt-0.5 text-xs text-text-faint">Every step in the tamper-evident chain for this incident, oldest first.</p>
        <ol className="mt-4 grid gap-4">
          {data.entries.map((e) => <EntryRow key={e.id} entry={e} />)}
          {data.entries.length === 0 && <li className="text-sm text-text-faint">No chained entries.</li>}
        </ol>
      </section>

      {data.trace && (
        <section className="panel p-5">
          <div className="eyebrow">Diagnosis</div>
          <p className="mt-2 text-sm text-color-text">“{data.trace.user_query}”</p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-dim">
            <span>asked {when(data.trace.created_at)}{data.trace.requested_by_role ? ` by a ${data.trace.requested_by_role}` : ""}</span>
            {data.trace.target_agent && <span>agent: {data.trace.target_agent}</span>}
            {data.trace.intent && <span>intent: {data.trace.intent}</span>}
            {data.trace.critic_verdict_status && <span>critic: {data.trace.critic_verdict_status}</span>}
            {data.trace.degraded && <span style={{ color: "var(--warn)" }}>ran degraded</span>}
          </div>
          {data.trace.steps.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-1 text-xs text-text-dim">
              {data.trace.steps.map((s, i) => (
                <span key={i} className="inline-flex items-center gap-1">
                  {i > 0 && <ChevronRight className="h-3 w-3 text-text-faint" />}
                  <span className="rounded px-1.5 py-0.5" style={{ background: "var(--canvas)" }}>
                    {s.node}{s.duration_ms != null ? ` ${Math.round(s.duration_ms)}ms` : ""}
                  </span>
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="panel p-5">
        <div className="eyebrow">Proposed fixes</div>
        <ul className="mt-3 grid gap-3">
          {data.proposals.map((p) => (
            <li key={p.id} className="text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <code className="break-all text-color-text">{p.command ?? "(no command)"}</code>
                <Pill color={riskColor(p.effective_risk)}>{p.effective_risk ?? "unknown"} risk</Pill>
                <Pill color={outcomeColor(p.status)}>{OUTCOME_LABEL[p.status] ?? p.status}</Pill>
              </div>
              {p.host && <div className="mt-0.5 text-xs text-text-faint">on {p.host}</div>}
            </li>
          ))}
        </ul>
      </section>

      <section className="panel p-5">
        <div className="eyebrow">Alerts open on that host meanwhile</div>
        <p className="mt-0.5 text-xs text-text-faint">Matched by host and time, shown for context. They are not part of the hash chain.</p>
        <ul className="mt-3 grid gap-2">
          {data.alerts.map((a) => <AlertRow key={a.id} alert={a} />)}
          {data.alerts.length === 0 && <li className="text-sm text-text-faint">None.</li>}
        </ul>
      </section>
    </div>
  );
}

export default function RemediationAuditView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const selected = params.get("trace");
  const [hours, setHours] = useState(24 * 7);

  const { data, error, isLoading, isValidating, mutate } = useSWR<IncidentSummary[]>(
    `/api/remediation/audit/incidents?hours=${hours}`, fetcher, { refreshInterval: 30000 },
  );

  const [verifying, setVerifying] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const verify = async () => {
    setVerifying(true);
    try {
      const r = await fetcher("/api/remediation/audit/verify");
      setVerifyMsg({
        ok: r.ok,
        text: r.ok ? `Whole chain verified: ${r.entries_checked} entries, no break.`
          : `Broken at entry ${r.broken_at}: ${r.reason}`,
      });
    } catch (e) {
      setVerifyMsg({ ok: false, text: e instanceof Error ? e.message : "Could not verify." });
    } finally {
      setVerifying(false);
    }
  };

  const select = (traceId: string) => router.replace(`${pathname}?trace=${encodeURIComponent(traceId)}`);
  const forbidden = error instanceof HttpError && error.status === 403;

  return (
    <div className="grid gap-4">
      <div className="glow-surface panel flex flex-wrap items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <ClipboardCheck className="h-[18px] w-[18px] text-text-dim" />
          </div>
          <div>
            <div className="eyebrow">Audit</div>
            <h1 className="font-display mt-1 text-lg font-semibold text-color-text">Remediation audit trail</h1>
            <p className="mt-0.5 text-sm text-text-faint">
              One incident, end to end: diagnosed, proposed, validated, executed. Tamper-evident.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            aria-label="Time range"
            className="rounded-[var(--radius-control)] px-2.5 py-2 text-sm"
            style={{ border: "1px solid var(--border)", background: "var(--canvas)", color: "var(--text)" }}
          >
            <option value={24}>Last 24 hours</option>
            <option value={24 * 7}>Last 7 days</option>
            <option value={24 * 30}>Last 30 days</option>
            <option value={24 * 365}>Last year</option>
          </select>
          <button
            onClick={() => mutate()}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-medium"
            style={{ border: "1px solid var(--border)", color: "var(--text)" }}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isValidating ? "animate-spin" : ""}`} /> Refresh
          </button>
          <button
            onClick={verify}
            disabled={verifying || forbidden}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-3 py-2 text-sm font-semibold text-white disabled:opacity-60"
            style={{ background: "var(--accent)" }}
          >
            <ShieldCheck className="h-3.5 w-3.5" /> {verifying ? "Verifying…" : "Verify chain"}
          </button>
        </div>
        {verifyMsg && (
          <p className="w-full text-sm" style={{ color: verifyMsg.ok ? "var(--ok)" : "var(--crit)" }}>{verifyMsg.text}</p>
        )}
      </div>

      {forbidden ? (
        <div className="panel flex items-start gap-2 p-5 text-sm" style={{ color: "var(--warn)" }}>
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          The audit trail is available to admins and operators. Your account is read-only.
        </div>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[320px_1fr]">
          <section className="panel p-3">
            <div className="eyebrow px-2 pb-2">Incidents</div>
            {isLoading && <div className="px-2 py-3 text-sm text-text-faint">Loading…</div>}
            {error && !forbidden && <div className="px-2 py-3 text-sm" style={{ color: "var(--crit)" }}>{error.message}</div>}
            {data && data.length === 0 && (
              <div className="px-2 py-3 text-sm text-text-faint">
                No incidents in this period. An incident appears when the AI copilot proposes a fix.
              </div>
            )}
            <ul className="grid gap-1">
              {data?.map((i) => {
                const active = i.trace_id === selected;
                return (
                  <li key={i.trace_id}>
                    <button
                      onClick={() => select(i.trace_id)}
                      className="w-full rounded-[var(--radius-control)] p-2.5 text-left transition-colors hover:bg-[var(--canvas)]"
                      style={{ background: active ? "var(--accent-soft)" : "transparent", border: `1px solid ${active ? "var(--accent)" : "transparent"}` }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-color-text">{i.hosts.join(", ") || "Unknown host"}</span>
                        <Pill color={outcomeColor(i.outcome)}>{OUTCOME_LABEL[i.outcome] ?? i.outcome}</Pill>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-text-dim">
                        {i.highest_risk && <span style={{ color: riskColor(i.highest_risk) }}>{i.highest_risk} risk</span>}
                        <span>{i.proposals} proposal{i.proposals === 1 ? "" : "s"}</span>
                        <span>· {i.last_at ? formatRelative(i.last_at) : "—"}</span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          {selected ? (
            <IncidentDetail traceId={selected} />
          ) : (
            <div className="panel p-8 text-center text-sm text-text-faint">
              Select an incident to see who diagnosed, proposed, approved and ran it, and why.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
