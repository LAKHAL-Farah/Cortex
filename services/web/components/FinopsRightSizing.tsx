"use client";

import { Fragment, useMemo, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, ChevronDown, ChevronRight, FlaskConical, Info } from "lucide-react";
import type {
  RightSizingClass,
  RightSizingConfidence,
  RightSizingRecommendation,
  RightSizingResponse,
  RightSizingRisk,
} from "@/lib/types";
import { formatEur, formatMb, formatPct, jsonFetcher } from "@/lib/quotas";
import { Card } from "./ui/Card";

const WINDOWS = [7, 14, 30];

const RISK_COLOR: Record<RightSizingRisk, string> = { low: "var(--ok)", medium: "var(--warn)", high: "var(--crit)" };
const RISK_SOFT: Record<RightSizingRisk, string> = { low: "var(--ok-soft)", medium: "var(--warn-soft)", high: "var(--crit-soft)" };

const CLASS_LABEL: Record<RightSizingClass, string> = {
  idle: "Idle",
  underutilized: "Under-utilised",
  right_sized: "Right-sized",
  saturated: "Saturated",
  insufficient_data: "Not enough history",
};

const CONFIDENCE_LABEL: Record<RightSizingConfidence, string> = { low: "Low", medium: "Medium", high: "High" };

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <div className="text-xs text-text-faint">{label}</div>
      <div className="stat-figure mt-0.5 text-[20px] text-color-text">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-text-faint">{sub}</div>}
    </Card>
  );
}

function RiskBadge({ level }: { level: RightSizingRisk }) {
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize"
      style={{ color: RISK_COLOR[level], background: RISK_SOFT[level] }}
    >
      {level} risk
    </span>
  );
}

/** 0-100 score as a thin bar: red when most of what the VM reserves is idle. */
function EfficiencyBar({ score }: { score: number | null }) {
  if (score === null) return <span className="text-text-faint">—</span>;
  const color = score >= 70 ? "var(--ok)" : score >= 40 ? "var(--warn)" : "var(--crit)";
  return (
    <div className="flex items-center justify-end gap-2" title={`Efficiency score ${score}/100`}>
      <div className="h-1.5 w-12 overflow-hidden rounded-full" style={{ background: "var(--border-soft)" }}>
        <div className="h-full rounded-full" style={{ width: `${Math.min(Math.max(score, 0), 100)}%`, background: color }} />
      </div>
      <span className="w-6 text-right text-text-dim">{Math.round(score)}</span>
    </div>
  );
}

function size(vcpus: number, ramMb: number): string {
  return `${vcpus} vCPU / ${formatMb(ramMb)}`;
}

function RecommendationRow({ rec, savingsKnown }: { rec: RightSizingRecommendation; savingsKnown: boolean }) {
  const [open, setOpen] = useState(false);
  const u = rec.utilization;
  return (
    <Fragment>
      <tr
        className="cursor-pointer align-top"
        style={{ borderBottom: open ? "none" : "1px solid var(--border-soft)" }}
        onClick={() => setOpen((v) => !v)}
      >
        <td className="px-4 py-2">
          <div className="flex items-start gap-1.5">
            {open ? (
              <ChevronDown className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-faint" />
            ) : (
              <ChevronRight className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-faint" />
            )}
            <div>
              <button
                type="button"
                className="text-left font-medium text-color-text"
                aria-expanded={open}
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen((v) => !v);
                }}
              >
                {rec.name}
              </button>
              <div className="text-xs text-text-faint">
                {rec.project_name ?? "—"} · {CLASS_LABEL[rec.classification]}
              </div>
            </div>
          </div>
        </td>
        <td className="px-3 py-2 text-text-dim">{size(rec.current.vcpus, rec.current.ram_mb)}</td>
        <td className="px-3 py-2 font-medium text-color-text">{size(rec.recommended.vcpus, rec.recommended.ram_mb)}</td>
        <td className="px-3 py-2 text-right text-text-dim">
          {formatPct(u.cpu_p95)} / {formatPct(u.cpu_max)}
        </td>
        <td className="px-3 py-2 text-right text-text-dim">
          {u.mem_p95 === null ? "—" : `${formatPct(u.mem_p95)} / ${formatPct(u.mem_max)}`}
        </td>
        <td className="px-3 py-2 text-right font-medium text-color-text">
          {savingsKnown ? formatEur(rec.estimated_monthly_savings_eur) : <span className="text-text-faint">unpriced</span>}
        </td>
        <td className="px-3 py-2 text-right">
          <RiskBadge level={rec.risk.level} />
        </td>
        <td className="px-4 py-2 text-right">
          <EfficiencyBar score={rec.efficiency_score} />
        </td>
      </tr>
      {open && (
        <tr style={{ borderBottom: "1px solid var(--border-soft)" }}>
          <td colSpan={8} className="px-4 pb-3 pt-0">
            <div className="ml-5 space-y-2 rounded-[var(--radius-control)] p-3 text-xs" style={{ background: "var(--surface-2, transparent)" }}>
              <p className="text-text-dim">{rec.rationale}</p>
              <div>
                <div className="mb-0.5 font-medium text-color-text">Why {rec.risk.level} risk</div>
                <ul className="list-disc space-y-0.5 pl-4 text-text-faint">
                  {rec.risk.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-1 text-text-faint">
                <span>Confidence: {CONFIDENCE_LABEL[rec.confidence]}</span>
                <span>Observed: {(u.observed_hours / 24).toFixed(1)} days</span>
                {rec.host && <span>Host: {rec.host}</span>}
                <span>
                  Cost: {formatEur(rec.current.monthly_cost_eur)} → {formatEur(rec.recommended.monthly_cost_eur)} / month
                  {rec.savings_pct !== null && ` (−${rec.savings_pct}%)`}
                </span>
                {rec.estimated_annual_savings_eur !== null && <span>≈ {formatEur(rec.estimated_annual_savings_eur, 0)} / year</span>}
              </div>
              {rec.notes.length > 0 && (
                <ul className="list-disc space-y-0.5 pl-4 text-text-faint">
                  {rec.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
              <p className="text-text-faint">{rec.action}</p>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function FinopsRightSizing() {
  const [windowDays, setWindowDays] = useState(7);
  const [demo, setDemo] = useState(false);
  const [risk, setRisk] = useState<RightSizingRisk | "all">("all");

  const qs = new URLSearchParams({ window_days: String(windowDays) });
  if (demo) qs.set("source", "synthetic");
  const { data, error, isLoading } = useSWR<RightSizingResponse>(`/api/rightsizing?${qs}`, jsonFetcher<RightSizingResponse>, {
    refreshInterval: 120000,
    keepPreviousData: true,
  });

  const recs = useMemo(() => {
    if (!data || !data.available) return [];
    return risk === "all" ? data.recommendations : data.recommendations.filter((r) => r.risk.level === risk);
  }, [data, risk]);

  const controls = (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <label className="text-xs text-text-faint">
        Observation window
        <select
          value={windowDays}
          onChange={(e) => setWindowDays(Number(e.target.value))}
          className="mt-1 block h-9 rounded-[var(--radius-control)] border bg-transparent px-2 text-sm text-color-text outline-none"
          style={{ borderColor: "var(--border)" }}
        >
          {WINDOWS.map((w) => (
            <option key={w} value={w} className="text-black">
              Last {w} days
            </option>
          ))}
        </select>
      </label>
      <label className="inline-flex h-9 items-center gap-2 text-xs text-text-faint">
        <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
        <FlaskConical className="h-3.5 w-3.5" strokeWidth={2} />
        Preview with demo data
      </label>
    </div>
  );

  if (error && !data) {
    return (
      <div className="space-y-4">
        {controls}
        <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
          <AlertTriangle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
          <span style={{ color: "var(--crit)" }}>Couldn&apos;t load right-sizing: {error.message}</span>
        </div>
      </div>
    );
  }
  if (isLoading || !data) return <p className="p-6 text-sm text-text-faint">Analysing VM utilisation…</p>;

  if (!data.available) {
    return (
      <div className="space-y-4">
        {controls}
        <div className="panel flex items-start gap-2 p-4 text-sm" style={{ borderColor: "var(--warn)" }}>
          <Info className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
          <div className="space-y-1 text-text-dim">
            <div className="font-medium text-color-text">No VM-level utilisation yet</div>
            <p>{data.reason}</p>
            <p className="text-text-faint">
              Cortex never estimates VM usage from host metrics. Tick &ldquo;Preview with demo data&rdquo; to see what the page looks like once a
              libvirt exporter is feeding Prometheus.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const s = data.summary;
  const savingsKnown = s.priced;
  const cls = s.by_classification;

  return (
    <div className="space-y-4">
      {controls}

      {data.source === "synthetic" && (
        <div className="panel flex items-start gap-2 p-3 text-sm" style={{ borderColor: "var(--crit)" }}>
          <FlaskConical className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
          <span className="text-text-dim">
            <strong className="text-color-text">Demo data.</strong> These VMs are a synthetic test fleet, not your cloud.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Reclaimable per month"
          value={savingsKnown ? formatEur(s.estimated_monthly_savings_eur) : "—"}
          sub={savingsKnown ? `≈ ${formatEur(s.estimated_annual_savings_eur, 0)} per year` : "unit rates not metered yet"}
        />
        <Stat
          label="Low-risk share"
          value={savingsKnown ? formatEur(s.low_risk_monthly_savings_eur) : "—"}
          sub={`${s.by_risk.low} low · ${s.by_risk.medium} medium · ${s.by_risk.high} high`}
        />
        <Stat label="VMs flagged" value={`${s.flagged} / ${s.vms_analyzed}`} sub={`${cls.idle} idle · ${cls.underutilized} under-utilised`} />
        <Stat
          label="Fleet efficiency"
          value={s.fleet_efficiency_score === null ? "—" : `${Math.round(s.fleet_efficiency_score)} / 100`}
          sub={cls.saturated ? `${cls.saturated} VM(s) saturated – may need more` : "cost-weighted, p95 vs target"}
        />
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-text-faint">Risk:</span>
        {(["all", "low", "medium", "high"] as const).map((r) => {
          const active = risk === r;
          return (
            <button
              key={r}
              type="button"
              onClick={() => setRisk(r)}
              aria-pressed={active}
              className="rounded-full border px-2.5 py-1 font-medium capitalize"
              style={{
                borderColor: active ? "var(--accent)" : "var(--border)",
                color: active ? "var(--text)" : "var(--text-faint)",
              }}
            >
              {r}
            </button>
          );
        })}
      </div>

      <Card padding="p-0" className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-left text-sm">
            <thead>
              <tr className="text-xs text-text-faint" style={{ borderBottom: "1px solid var(--border-soft)" }}>
                <th className="px-4 py-2 font-medium">VM</th>
                <th className="px-3 py-2 font-medium">Current</th>
                <th className="px-3 py-2 font-medium">Suggested</th>
                <th className="px-3 py-2 text-right font-medium">CPU p95 / max</th>
                <th className="px-3 py-2 text-right font-medium">Memory p95 / max</th>
                <th className="px-3 py-2 text-right font-medium">Saving / month</th>
                <th className="px-3 py-2 text-right font-medium">Risk</th>
                <th className="px-4 py-2 text-right font-medium">Efficiency</th>
              </tr>
            </thead>
            <tbody>
              {recs.map((r) => (
                <RecommendationRow key={r.vm_id} rec={r} savingsKnown={savingsKnown} />
              ))}
              {recs.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-sm text-text-faint">
                    {data.recommendations.length === 0
                      ? "No over-provisioned VMs found in this window."
                      : `No ${risk}-risk recommendations.`}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {data.others.length > 0 && (
        <Card padding="p-4" className="text-xs text-text-faint">
          <div className="mb-1 font-medium text-text-dim">Not flagged ({data.others.length})</div>
          <ul className="grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
            {data.others.map((o) => (
              <li key={o.vm_id} className="flex justify-between gap-3">
                <span className="truncate text-text-dim">{o.name}</span>
                <span>{CLASS_LABEL[o.classification]}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card padding="p-4" className="text-xs text-text-faint">
        <div className="mb-1 font-medium text-text-dim">How to read this</div>
        <ul className="list-disc space-y-0.5 pl-4">
          <li>Recommendations only: Cortex never resizes a VM. Apply changes in a change window once the owner agrees.</li>
          <li>
            Sizes target ≤ 60% CPU / ≤ 75% memory at p95 and keep the observed peak under 85% of the new size.
            {data.flavor_catalog === "ladder" && " No Nova flavor list was available, so suggested sizes are generic: pick the closest existing flavor that is at least that big."}
          </li>
          <li>{data.caveat}</li>
        </ul>
      </Card>
    </div>
  );
}
