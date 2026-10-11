"use client";

import React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { ArrowRight, Plus, Sparkles } from "lucide-react";
import type { QuotaAlert } from "@/lib/types";
import { serviceDisplayName } from "@/lib/anomalies";
import { metricLabel, thresholdEtaLabel } from "@/lib/thresholds";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { apiFetcher, copilotHref, useFleet, useFleetTrend, useForecastWarnings, useIncidents, useLogs } from "@/lib/useDashboardData";

type Tone = "ok" | "warn" | "crit";
const TONE: Record<Tone, { color: string; soft: string; label: string }> = {
  ok: { color: "var(--ok)", soft: "var(--ok-soft)", label: "All systems nominal" },
  warn: { color: "var(--warn)", soft: "var(--warn-soft)", label: "Needs attention" },
  crit: { color: "var(--crit)", soft: "var(--crit-soft)", label: "Action required" },
};

const BUCKETS = 12; // 12 x 5 min = last hour
const BUCKET_MS = 300_000;

/* ── tiny pieces ─────────────────────────────────────────────────────────── */

function Spark({ id, data, color }: { id: string; data: { v: number }[]; color: string }) {
  if (data.length < 2) return <div className="h-8" />;
  const gid = `cxk-${id}`;
  return (
    <ResponsiveContainer width="100%" height={32}>
      <AreaChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={`url(#${gid})`} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function Kpi({
  id, label, value, sub, href, status, spark, sparkColor = "var(--medium)", bar, detail,
}: {
  id: string; label: string; value: string | number; sub: string; href: string;
  status: string; sparkColor?: string;
  spark?: { v: number }[]; bar?: number; detail?: string;
}) {
  return (
    <Link
      href={href}
      className="cx-kpi flex min-w-0 flex-col justify-between gap-1.5 p-3.5"
      // Only non-ok tiles glow, so the glow itself means "look here".
      style={status === "var(--ok)" ? undefined : { ["--glow" as string]: status }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[11px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--h-faint)" }}>{label}</span>
        <span className="status-dot" style={{ background: status }} />
      </div>
      <div className="stat-figure text-[24px] leading-7" style={{ color: "var(--h-text)" }}>{value}</div>
      {bar !== undefined ? (
        <div className="cx-track my-1.5" style={{ ["--bar" as string]: status === "var(--ok)" ? "var(--accent)" : status }}><span style={{ width: `${Math.max(2, Math.min(100, bar))}%` }} /></div>
      ) : spark ? (
        <Spark id={id} data={spark} color={sparkColor} />
      ) : (
        <div className="line-clamp-2 flex h-8 items-center text-xs leading-4" style={{ color: "var(--h-dim)" }}>{detail}</div>
      )}
      <div className="truncate text-[11px]" style={{ color: "var(--h-faint)" }}>{sub}</div>
    </Link>
  );
}

function ActivityChart({ logs }: { logs: { ts: number; level: string | null }[] }) {
  const [hover, setHover] = React.useState<number | null>(null);
  const buckets = React.useMemo(() => {
    if (!logs.length) return [];
    const end = Math.ceil(logs[0].ts / BUCKET_MS) * BUCKET_MS; // logs are newest-first
    return Array.from({ length: BUCKETS }, (_, i) => {
      const lo = end - (BUCKETS - i) * BUCKET_MS;
      const inB = logs.filter((l) => l.ts >= lo && l.ts < lo + BUCKET_MS);
      return { lo, total: inB.length, errors: inB.filter((l) => l.level === "ERROR").length, warns: inB.filter((l) => l.level === "WARNING").length };
    });
  }, [logs]);

  const max = Math.max(1, ...buckets.map((b) => b.total));
  const active = hover ?? buckets.length - 1;
  const fmt = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const sel = buckets[active];

  return (
    <Link href="/logs" className="cx-glass flex min-w-0 flex-col p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.12em]" style={{ color: "var(--h-faint)" }}>Activity · last hour</div>
          <div className="mt-1 text-xs" style={{ color: "var(--h-dim)" }}>Log lines per 5 min</div>
        </div>
        <div className="flex items-center gap-2 text-[10.5px]" style={{ color: "var(--h-faint)" }}>
          <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: "var(--medium)" }} />lines</span>
          <span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-sm" style={{ background: "var(--crit)" }} />errors</span>
        </div>
      </div>

      <div className="mt-4 flex h-[118px] items-end gap-1.5" onMouseLeave={() => setHover(null)}>
        {buckets.length === 0 ? (
          <div className="grid h-full w-full place-items-center text-xs" style={{ color: "var(--h-faint)" }}>Waiting for logs…</div>
        ) : (
          buckets.map((b, i) => (
            <div key={b.lo} className={`cx-bar ${i === active ? "cx-bar--active" : ""}`} onMouseEnter={() => setHover(i)}>
              {i === active && (
                <span className="cx-tip">
                  {fmt(b.lo)} · {b.total} lines{b.errors ? ` · ${b.errors} err` : ""}
                </span>
              )}
              <div className="cx-bar__fill" style={{ height: `${Math.max(6, (b.total / max) * 100)}%` }}>
                {b.errors > 0 && <div className="cx-bar__err" style={{ height: `${(b.errors / Math.max(1, b.total)) * 100}%` }} />}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="mt-2 flex justify-between text-[10.5px]" style={{ color: "var(--h-faint)" }}>
        <span>{buckets[0] ? fmt(buckets[0].lo) : "—"}</span>
        <span>{sel ? `${sel.warns} warnings` : ""}</span>
        <span>now</span>
      </div>
    </Link>
  );
}

/* ── main ────────────────────────────────────────────────────────────────── */

/** Top-of-page command header: AI briefing + Copilot prompt, six KPI tiles and
 * an activity chart on one dark gradient canopy. Sentences are composed from
 * live data (no LLM call) so they are instant and always match the widgets
 * below; the prompt box and chips hand off to the Copilot. */
export default function CommandHeader() {
  const router = useRouter();
  const { user } = useCurrentUser();
  const { data: nodes } = useFleet();
  const { data: trend } = useFleetTrend(nodes);
  const { data: incidents } = useIncidents();
  const { data: warnings } = useForecastWarnings();
  const { data: quotas } = useSWR<QuotaAlert[]>("/api/quotas/alerts", apiFetcher, { refreshInterval: 30000, shouldRetryOnError: false });
  const { parsed } = useLogs();
  const [prompt, setPrompt] = React.useState("");

  const total = nodes?.length ?? 0;
  const upCount = (nodes ?? []).filter((n) => n.metrics?.status === "up").length;
  const down = (nodes ?? []).filter((n) => n.metrics?.status !== "up");
  const critIncidents = (incidents ?? []).filter((i) => i.severity === "critical");
  const quotaBreaches = (quotas ?? []).filter((q) => q.severity !== "normal");
  const errors = parsed.filter((l) => l.level === "ERROR").length;
  const warns = parsed.filter((l) => l.level === "WARNING").length;
  const topWarning = warnings?.[0];
  const topIncident = [...(incidents ?? [])].sort((a, b) => (b.severity === "critical" ? 1 : 0) - (a.severity === "critical" ? 1 : 0))[0];

  const tone: Tone = down.length || critIncidents.length ? "crit" : incidents?.length || quotaBreaches.length || warnings?.length || errors ? "warn" : "ok";
  const t = TONE[tone];

  const lines: string[] = [];
  if (total) lines.push(down.length ? `${down.length} of ${total} nodes are not responding (${down.slice(0, 3).map((n) => n.hostname).join(", ")}).` : `All ${total} nodes are reporting normally.`);
  if (incidents?.length) {
    const subject = topIncident?.root_cause_guess ? serviceDisplayName(topIncident.root_cause_guess.vertex_id) : "the fleet";
    lines.push(`${incidents.length} active incident${incidents.length === 1 ? "" : "s"}${critIncidents.length ? `, ${critIncidents.length} critical` : ""} — most likely rooted at ${subject}.`);
  } else if (incidents) lines.push("No active incidents.");
  if (topWarning) lines.push(`${metricLabel(topWarning.metric)} on ${topWarning.hostname} is projected to hit ${topWarning.threshold}% ${thresholdEtaLabel(topWarning)}.`);
  if (quotaBreaches.length) lines.push(`${quotaBreaches.length} quota or budget limit${quotaBreaches.length === 1 ? "" : "s"} breached.`);
  if (errors) lines.push(`${errors} error log line${errors === 1 ? "" : "s"} in the last hour.`);

  const chips: string[] = [];
  if (topIncident) chips.push(`Investigate the current ${topIncident.severity} incident and suggest a fix`);
  if (topWarning) chips.push(`Why is ${metricLabel(topWarning.metric).toLowerCase()} rising on ${topWarning.hostname}?`);
  chips.push("Summarize the last 24 hours of infrastructure health");
  if (chips.length < 3) chips.push("Which nodes are the most at risk this week?");

  const go = (text: string) => {
    const v = text.trim();
    router.push(v ? copilotHref(v) : "/copilot");
  };

  // KPI numbers
  const avg = (key: "cpu_percent" | "memory_percent") =>
    total ? Math.round(((nodes ?? []).reduce((s, n) => s + (n.metrics?.[key] ?? 0), 0) / total) * 10) / 10 : 0;
  const cpu = avg("cpu_percent");
  const mem = avg("memory_percent");
  const load = (v: number) => (v >= 90 ? "var(--crit)" : v >= 75 ? "var(--warn)" : "var(--ok)");
  const incCount = incidents?.length ?? 0;
  const soonest = warnings?.[0];
  const end = parsed[0]?.ts ?? 0; // logs are newest-first
  const errSeries = Array.from({ length: BUCKETS }, (_, i) => {
    const lo = end - (BUCKETS - i) * BUCKET_MS;
    return { v: parsed.filter((l) => l.level === "ERROR" && l.ts >= lo && l.ts < lo + BUCKET_MS).length };
  });

  return (
    <section className="cx-hero p-5 sm:p-6">
      {/* briefing + copilot */}
      <div className="grid gap-5 lg:grid-cols-[1.35fr_1fr]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: "var(--h-faint)" }}>Cortex briefing</span>
            <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium" style={{ color: t.color, background: t.soft }}>
              <span className="status-dot" style={{ background: t.color }} />
              {t.label}
            </span>
          </div>
          <h2 className="font-display mt-2 text-[22px] font-semibold leading-8" style={{ color: "var(--h-text)" }}>
            {user?.username ? `Here's your cloud right now, ${user.username}.` : "Here's your cloud right now."}
          </h2>
          <ul className="mt-3 space-y-1.5 text-sm leading-6" style={{ color: "var(--h-dim)" }}>
            {lines.length === 0 ? <li style={{ color: "var(--h-faint)" }}>Collecting live data…</li> : lines.slice(0, 4).map((l, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-2.5 h-1 w-1 flex-shrink-0 rounded-full" style={{ background: "var(--text-muted)" }} />
                {l}
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
            <span className="cx-glass inline-flex items-center gap-1.5 px-2.5 py-1.5" style={{ color: "var(--h-dim)" }}>
              <span className="status-dot" style={{ background: "var(--accent)", boxShadow: "0 0 8px var(--accent)" }} />
              Live · refreshes every 5s
            </span>
            <Link href="/nodes" className="cx-cta inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 font-medium transition-opacity hover:opacity-90">
              <Plus className="h-3.5 w-3.5" strokeWidth={2} />
              Add node
            </Link>
          </div>
        </div>

        <div className="cx-glass p-3.5">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium" style={{ color: "var(--h-dim)" }}>
            <Sparkles className="h-3.5 w-3.5" style={{ color: "var(--accent)" }} strokeWidth={1.75} />
            Ask Cortex Copilot
          </div>
          <div className="flex gap-2">
            <input
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && go(prompt)}
              placeholder="e.g. Why is compute1 slow?"
              aria-label="Ask the Copilot"
              className="cx-input min-w-0 flex-1 rounded-lg px-3 py-2 text-sm"
            />
            <button onClick={() => go(prompt)} aria-label="Send to Copilot" className="cx-cta grid h-9 w-9 flex-shrink-0 place-items-center rounded-lg transition-opacity hover:opacity-90">
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </button>
          </div>
          <div className="mt-2.5 flex flex-col gap-1.5">
            {chips.slice(0, 3).map((c) => (
              <button
                key={c}
                onClick={() => go(c)}
                title={c}
                className="truncate rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-[var(--accent-soft)]"
                style={{ color: "var(--h-dim)", border: "1px solid var(--border-soft)", background: "var(--surface)" }}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* KPIs + activity */}
      <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_300px] xl:grid-cols-[1fr_340px]">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
          <Kpi id="nodes" label="Nodes online" value={`${upCount}/${total}`} sub={total - upCount ? `${total - upCount} not responding` : "All responding"} href="/nodes" status={total - upCount ? "var(--crit)" : "var(--ok)"} bar={total ? (upCount / total) * 100 : 0} />
          <Kpi id="cpu" label="Avg CPU" value={`${cpu}%`} sub="Fleet · last hour" href="/metrics" status={load(cpu)} spark={(trend ?? []).map((p) => ({ v: p.cpu }))} sparkColor="var(--medium)" />
          <Kpi id="mem" label="Avg memory" value={`${mem}%`} sub="Fleet · last hour" href="/metrics" status={load(mem)} spark={(trend ?? []).map((p) => ({ v: p.mem }))} sparkColor="var(--accent)" />
          <Kpi id="inc" label="Incidents" value={incCount} sub={incCount ? `${critIncidents.length} critical` : "None active"} href="/alerts"
            status={incCount ? "var(--crit)" : "var(--ok)"}
            detail={incCount ? `Likely root: ${incidents![0].root_cause_guess ? serviceDisplayName(incidents![0].root_cause_guess.vertex_id) : incidents![0].members[0]?.hostname ?? "unknown"}` : "Everything within baseline"} />
          <Kpi id="fc" label="Forecast risks" value={warnings?.length ?? 0} sub="Threshold breaches ahead" href="/forecast"
            status={warnings?.length ? "var(--warn)" : "var(--ok)"}
            detail={soonest ? `${soonest.hostname} · ${soonest.metric.replace(/_percent$/, "")} ${soonest.already_breached ? "over threshold" : `in ~${Math.max(1, Math.round(soonest.eta_days ?? 0))}d`}` : "No breaches projected"} />
          <Kpi id="err" label="Log errors" value={errors} sub={`${warns} warnings · 1h`} href="/logs"
            status={errors ? "var(--crit)" : warns ? "var(--warn)" : "var(--ok)"} spark={errSeries} sparkColor="var(--crit)" />
        </div>
        <ActivityChart logs={parsed} />
      </div>
    </section>
  );
}
