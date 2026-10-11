"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Download, FileSpreadsheet, FileText, Info, Loader2 } from "lucide-react";
import type { FinopsReport, ReportVariant, ReportVariantInfo } from "@/lib/types";
import { formatEur, formatPct, jsonFetcher, ratioColor } from "@/lib/quotas";
import { Card } from "./ui/Card";

function monthLabel(period: string): string {
  const [y, m] = period.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en", { month: "long", year: "numeric", timeZone: "UTC" });
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <div className="text-xs text-text-faint">{label}</div>
      <div className="stat-figure mt-0.5 text-[20px] text-color-text">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-text-faint">{sub}</div>}
    </Card>
  );
}

function ExportButton({ href, icon: Icon, children }: { href: string; icon: typeof Download; children: React.ReactNode }) {
  return (
    <a
      href={href}
      download
      className="inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-control)] border px-3 text-sm font-medium text-color-text"
      style={{ borderColor: "var(--border)" }}
    >
      <Icon className="h-3.5 w-3.5" strokeWidth={2} />
      {children}
    </a>
  );
}


const VARIANTS: ReportVariantInfo[] = [
  {
    id: "executive",
    title: "Executive summary",
    audience: "Management",
    description: "Posture, headline cost and risk KPIs, decisions needed and top savings. One page.",
  },
  {
    id: "financial",
    title: "Financial report",
    audience: "Finance / project owners",
    description: "Showback and chargeback by department and project, budgets, projections and right-sizing savings.",
  },
  {
    id: "technical",
    title: "Technical report",
    audience: "Operations / engineering",
    description: "Capacity per node, forecast breaches, security findings, anomalies, agent actions and VM sizing detail.",
  },
];

/** Fetches the PDF (rather than a bare <a download>) so the user sees that
 * generation is running -- the operational variants query Prometheus -- and
 * gets the API's real error message if it fails. */
function ReportCard({ info, period, demo }: { info: ReportVariantInfo; period: string; demo: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ai, setAi] = useState(false);

  async function download(variant: ReportVariant) {
    setBusy(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ period });
      if (variant === "executive" && ai) qs.set("ai", "true");
      if (demo) qs.set("rightsizing_source", "synthetic");
      const res = await fetch(`/api/reports/${variant}/pdf?${qs}`);
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error((body && (body.detail || body.message)) || `Request failed (${res.status})`);
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `cortex-${variant}-report-${period}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Report generation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col justify-between gap-3">
      <div>
        <div className="text-sm font-semibold text-color-text">{info.title}</div>
        <div className="text-xs text-text-faint">For {info.audience}</div>
        <p className="mt-2 text-xs text-text-dim">{info.description}</p>
      </div>
      <div className="space-y-2">
        {info.id === "executive" && (
          <label className="flex items-center gap-1.5 text-xs text-text-faint">
            <input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />
            AI-written summary (slower)
          </label>
        )}
        <button
          type="button"
          onClick={() => download(info.id)}
          disabled={busy}
          className="inline-flex h-9 items-center gap-1.5 rounded-[var(--radius-control)] border px-3 text-sm font-medium text-color-text disabled:opacity-60"
          style={{ borderColor: "var(--border)" }}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} /> : <FileText className="h-3.5 w-3.5" strokeWidth={2} />}
          {busy ? "Generating…" : "Download PDF"}
        </button>
        {error && (
          <p className="text-xs" role="alert" style={{ color: "var(--crit)" }}>
            {error}
          </p>
        )}
      </div>
    </Card>
  );
}

export default function FinopsReports() {
  const [demoVms, setDemoVms] = useState(false);
  const [period, setPeriod] = useState<string>("");
  const key = `/api/quotas/report${period ? `?period=${period}` : ""}`;
  const { data, error, isLoading } = useSWR<FinopsReport>(key, jsonFetcher<FinopsReport>, {
    refreshInterval: 60000,
    keepPreviousData: true,
  });

  if (error && !data) {
    return (
      <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
        <AlertTriangle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
        <span style={{ color: "var(--crit)" }}>Couldn&apos;t load the report: {error.message}</span>
      </div>
    );
  }
  if (isLoading || !data) return <p className="p-6 text-sm text-text-faint">Loading…</p>;

  const qs = `?period=${data.period}`;
  const periods = Array.from(new Set([data.period, ...data.available_periods])).sort().reverse();
  const empty = data.projects.length === 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <label className="text-xs text-text-faint">
            Period
            <select
              value={data.period}
              onChange={(e) => setPeriod(e.target.value)}
              className="mt-1 block h-9 rounded-[var(--radius-control)] border bg-transparent px-2 text-sm text-color-text outline-none"
              style={{ borderColor: "var(--border)" }}
            >
              {periods.map((p) => (
                <option key={p} value={p} className="text-black">
                  {monthLabel(p)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="flex gap-2">
          <ExportButton href={`/api/quotas/report/pdf${qs}`} icon={FileText}>
            Export PDF
          </ExportButton>
          <ExportButton href={`/api/quotas/report/csv${qs}`} icon={FileSpreadsheet}>
            Export CSV
          </ExportButton>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-color-text">Professional reports</div>
            <div className="text-xs text-text-faint">
              Generated on demand for {monthLabel(data.period)}. Operational figures (capacity, security, alerts) always cover the last 7 days.
            </div>
          </div>
          <label className="inline-flex items-center gap-1.5 text-xs text-text-faint">
            <input type="checkbox" checked={demoVms} onChange={(e) => setDemoVms(e.target.checked)} />
            Use demo VM data (labelled in the PDF)
          </label>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          {VARIANTS.map((v) => (
            <ReportCard key={v.id} info={v} period={data.period} demo={demoVms} />
          ))}
        </div>
      </div>

      {data.coverage_pct < 99 && (
        <div className="panel flex items-start gap-2 p-3 text-sm" style={{ borderColor: "var(--warn)" }}>
          <Info className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
          <span className="text-text-dim">
            Metering covers {formatPct(data.coverage_pct, 1)} of {data.is_current_period ? "the hours elapsed so far" : "this period"} (
            {data.hours_metered} h). Totals only include metered hours.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Charged to projects (chargeback)" value={formatEur(data.totals.fully_loaded_eur)} sub="direct + platform + idle share" />
        <Stat label="Direct reserved cost (showback)" value={formatEur(data.totals.direct_eur)} sub="what projects reserved" />
        <Stat
          label={data.is_current_period ? "Projected month-end" : "Fixed bill (metered)"}
          value={formatEur(data.is_current_period ? data.totals.projected_month_end_eur : data.totals.cloud_bill_metered_eur)}
          sub={data.is_current_period ? "at current allocation" : "for the metered hours"}
        />
        <Stat label="Idle + platform" value={formatEur(data.totals.idle_eur + data.totals.platform_eur)} sub="spread across projects" />
      </div>

      <Card padding="p-0" className="overflow-hidden">
        <div className="px-4 pt-4 text-sm font-semibold text-color-text">Cost by department</div>
        <div className="overflow-x-auto">
          <table className="mt-2 w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="text-xs text-text-faint" style={{ borderBottom: "1px solid var(--border-soft)" }}>
                <th className="px-4 py-2 font-medium">Department</th>
                <th className="px-3 py-2 text-right font-medium">Projects</th>
                <th className="px-3 py-2 text-right font-medium">Direct</th>
                <th className="px-3 py-2 text-right font-medium">Platform</th>
                <th className="px-3 py-2 text-right font-medium">Idle</th>
                <th className="px-3 py-2 text-right font-medium">Total charge</th>
                <th className="px-4 py-2 text-right font-medium">Share</th>
              </tr>
            </thead>
            <tbody>
              {data.departments.map((d) => (
                <tr key={d.department} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                  <td className="px-4 py-2 font-medium text-color-text">{d.department}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{d.projects}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{formatEur(d.direct_eur)}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{formatEur(d.platform_eur)}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{formatEur(d.idle_eur)}</td>
                  <td className="px-3 py-2 text-right font-medium text-color-text">{formatEur(d.fully_loaded_eur)}</td>
                  <td className="px-4 py-2 text-right text-text-faint">{formatPct(d.share_pct, 1)}</td>
                </tr>
              ))}
              {empty && (
                <tr>
                  <td colSpan={7} className="px-4 py-6 text-center text-sm text-text-faint">
                    No metered usage in {monthLabel(data.period)}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card padding="p-0" className="overflow-hidden">
        <div className="px-4 pt-4 text-sm font-semibold text-color-text">Cost by project</div>
        <div className="overflow-x-auto">
          <table className="mt-2 w-full min-w-[900px] text-left text-sm">
            <thead>
              <tr className="text-xs text-text-faint" style={{ borderBottom: "1px solid var(--border-soft)" }}>
                <th className="px-4 py-2 font-medium">Project</th>
                <th className="px-3 py-2 font-medium">Department</th>
                <th className="px-3 py-2 text-right font-medium">Avg vCPU</th>
                <th className="px-3 py-2 text-right font-medium">Avg RAM</th>
                <th className="px-3 py-2 text-right font-medium">Avg disk</th>
                <th className="px-3 py-2 text-right font-medium">Direct</th>
                <th className="px-3 py-2 text-right font-medium">Platform + idle</th>
                <th className="px-3 py-2 text-right font-medium">Total charge</th>
                <th className="px-4 py-2 text-right font-medium">Budget used</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((p) => (
                <tr key={p.project_id} style={{ borderBottom: "1px solid var(--border-soft)" }}>
                  <td className="px-4 py-2 font-medium text-color-text">{p.project_name}</td>
                  <td className="px-3 py-2 text-text-dim">{p.department}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{p.avg_vcpus}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{p.avg_ram_gb} GB</td>
                  <td className="px-3 py-2 text-right text-text-dim">{p.avg_storage_gb} GB</td>
                  <td className="px-3 py-2 text-right text-text-dim">{formatEur(p.direct_eur)}</td>
                  <td className="px-3 py-2 text-right text-text-dim">{formatEur(p.platform_eur + p.idle_eur)}</td>
                  <td className="px-3 py-2 text-right font-medium text-color-text">{formatEur(p.fully_loaded_eur)}</td>
                  <td className="px-4 py-2 text-right" style={{ color: ratioColor(p.budget_used_ratio) }}>
                    {p.budget_used_ratio === null ? <span className="text-text-faint">—</span> : formatPct(p.budget_used_ratio * 100)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card padding="p-4" className="text-xs text-text-faint">
        <div className="mb-1 font-medium text-text-dim">How these numbers are computed</div>
        <ul className="list-disc space-y-0.5 pl-4">
          {data.method.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
