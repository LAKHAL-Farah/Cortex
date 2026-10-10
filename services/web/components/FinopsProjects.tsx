"use client";

import { useMemo, useState } from "react";
import { useSWRConfig } from "swr";
import { AlertTriangle, Check, Loader2, Pencil, Search, X } from "lucide-react";
import type { FinopsProject, FinopsUsageRow, QuotaSeverity } from "@/lib/types";
import {
  QUOTA_SEVERITY_COLOR,
  QUOTA_SEVERITY_LABEL,
  QUOTA_SEVERITY_SOFT,
  formatCount,
  formatEur,
  formatMb,
  formatPct,
  quotaFill,
  ratioColor,
} from "@/lib/quotas";
import { useCurrentUser } from "@/lib/useCurrentUser";
import { Card } from "./ui/Card";
import { ProgressBar } from "./ui/ProgressBar";
import { useFinopsOverview } from "./FinopsOverview";

/** One resource cell: used / quota with a bar tinted by how close the project
 * is to *its own* quota (the capacity_cap view), plus the project's share of
 * the whole cloud underneath (the "who is using the platform" view). */
function UsageCell({
  row,
  fmt,
  cloudPct,
}: {
  row: FinopsUsageRow;
  fmt: (v: number) => string;
  cloudPct: number | null;
}) {
  return (
    <div className="min-w-[120px]">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-color-text">{fmt(row.used)}</span>
        <span className="text-text-faint">{row.quota === null ? "no quota" : `/ ${fmt(row.quota)}`}</span>
      </div>
      <div className="mt-1">
        <ProgressBar value={row.quota ? quotaFill(row.quota_ratio ?? 0) : 0} color={ratioColor(row.quota_ratio)} />
      </div>
      <div className="mt-0.5 text-[11px] text-text-faint">
        {cloudPct === null ? "—" : `${formatPct(cloudPct, 1)} of cloud`}
      </div>
    </div>
  );
}

function StatusBadge({ severity }: { severity: QuotaSeverity }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color: QUOTA_SEVERITY_COLOR[severity], background: QUOTA_SEVERITY_SOFT[severity] }}
    >
      <span className="status-dot" style={{ background: QUOTA_SEVERITY_COLOR[severity] }} />
      {severity === "normal" ? "Healthy" : QUOTA_SEVERITY_LABEL[severity]}
    </span>
  );
}

function EditRow({ project, onDone }: { project: FinopsProject; onDone: () => void }) {
  const { mutate } = useSWRConfig();
  const [department, setDepartment] = useState(project.department === "Unassigned" ? "" : project.department);
  const [budget, setBudget] = useState(project.budget_eur ? String(project.budget_eur) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const parsed = budget.trim() === "" ? null : Number(budget);
    if (parsed !== null && (Number.isNaN(parsed) || parsed < 0)) {
      setError("Budget must be a positive number (or empty for none).");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/quotas/projects/${encodeURIComponent(project.project_id)}/settings`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ department: department.trim() || null, monthly_budget_eur: parsed }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error((data && data.detail) || `Save failed (${res.status})`);
      // Re-run the check so budget alerts + report grouping pick it up now,
      // not at the next periodic pass. Best-effort: the save itself succeeded.
      await fetch("/api/quotas/resync", { method: "POST" }).catch(() => null);
      await Promise.all([mutate("/api/quotas/overview"), mutate("/api/quotas/alerts")]);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const inputCls = "h-8 rounded-[var(--radius-control)] border bg-transparent px-2 text-sm text-color-text outline-none";
  return (
    <tr style={{ background: "var(--accent-soft)" }}>
      <td colSpan={8} className="px-4 py-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="text-sm font-semibold text-color-text">{project.project_name}</div>
          <label className="text-xs text-text-faint">
            Department
            <input
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder="e.g. Engineering"
              maxLength={80}
              className={`${inputCls} mt-1 block w-44`}
              style={{ borderColor: "var(--border)" }}
            />
          </label>
          <label className="text-xs text-text-faint">
            Monthly budget (€)
            <input
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              inputMode="decimal"
              placeholder="none"
              className={`${inputCls} mt-1 block w-32`}
              style={{ borderColor: "var(--border)" }}
            />
          </label>
          <button
            onClick={save}
            disabled={saving}
            className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] px-3 text-sm font-medium"
            style={{ background: "var(--accent)", color: "var(--bg, #fff)" }}
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
            Save
          </button>
          <button
            onClick={onDone}
            disabled={saving}
            className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] border px-3 text-sm"
            style={{ borderColor: "var(--border)" }}
          >
            <X className="h-3.5 w-3.5" /> Cancel
          </button>
          {error && <span className="text-xs" style={{ color: "var(--crit)" }}>{error}</span>}
        </div>
        <p className="mt-2 text-xs text-text-faint">
          The budget is compared with what the project itself has reserved (not its share of idle capacity), so it only
          fires on the project&apos;s own decisions. Leave it empty for no budget alert.
        </p>
      </td>
    </tr>
  );
}

export default function FinopsProjects() {
  const { data, error, isLoading } = useFinopsOverview();
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<string | null>(null);

  const projects = useMemo(() => (data && data.available ? data.projects : []), [data]);
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return projects.filter(
      (p) =>
        !q ||
        p.project_name.toLowerCase().includes(q) ||
        p.project_id.toLowerCase().includes(q) ||
        p.department.toLowerCase().includes(q)
    );
  }, [projects, search]);

  if (isLoading) return <p className="p-6 text-sm text-text-faint">Loading…</p>;
  if (error) {
    return (
      <div className="panel flex items-center gap-2 p-4 text-sm" style={{ borderColor: "var(--crit)" }}>
        <AlertTriangle className="h-4 w-4 flex-shrink-0" style={{ color: "var(--crit)" }} strokeWidth={2} />
        <span style={{ color: "var(--crit)" }}>Couldn&apos;t reach the FinOps service: {error.message}</span>
      </div>
    );
  }
  if (!data || !data.available) {
    return <p className="p-6 text-sm text-text-faint">No metering data yet — press Check now.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-faint" strokeWidth={2} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search project or department…"
            className="h-9 w-full rounded-[var(--radius-control)] border bg-transparent pl-8 pr-3 text-sm text-color-text outline-none"
            style={{ borderColor: "var(--border)" }}
          />
        </div>
        <span className="text-xs text-text-faint">
          {filtered.length} project{filtered.length === 1 ? "" : "s"} · costs are monthly run-rates of what each project has reserved now
        </span>
      </div>

      <Card padding="p-0" className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-left text-sm">
            <thead>
              <tr className="text-xs text-text-faint" style={{ borderBottom: "1px solid var(--border-soft)" }}>
                <th className="px-4 py-3 font-medium">Project</th>
                <th className="px-3 py-3 font-medium">Department</th>
                <th className="px-3 py-3 font-medium">vCPUs</th>
                <th className="px-3 py-3 font-medium">RAM</th>
                <th className="px-3 py-3 font-medium">Storage</th>
                <th className="px-3 py-3 text-right font-medium whitespace-nowrap">Cost / month</th>
                <th className="px-3 py-3 font-medium">Budget</th>
                <th className="px-3 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <ProjectRows
                  key={p.project_id}
                  p={p}
                  isAdmin={isAdmin}
                  editing={editing === p.project_id}
                  onEdit={() => setEditing(p.project_id)}
                  onDone={() => setEditing(null)}
                />
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-sm text-text-faint">
                    No projects match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
      {!isAdmin && (
        <p className="text-xs text-text-faint">Only admins can edit a project&apos;s department and budget.</p>
      )}
    </div>
  );
}

function ProjectRows({
  p,
  isAdmin,
  editing,
  onEdit,
  onDone,
}: {
  p: FinopsProject;
  isAdmin: boolean;
  editing: boolean;
  onEdit: () => void;
  onDone: () => void;
}) {
  return (
    <>
      <tr className="align-top" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <td className="px-4 py-3">
          <div className="font-semibold text-color-text">{p.project_name}</div>
          <div className="max-w-[180px] truncate text-xs text-text-faint">{p.project_id}</div>
          <div className="text-xs text-text-faint">{formatCount(p.usage.instances.used)} VM{p.usage.instances.used === 1 ? "" : "s"}</div>
        </td>
        <td className="px-3 py-3 text-text-dim">
          {p.department === "Unassigned" ? <span className="text-text-faint">Unassigned</span> : p.department}
        </td>
        <td className="px-3 py-3">
          <UsageCell row={p.usage.vcpus} fmt={formatCount} cloudPct={p.cloud_share.vcpus_pct} />
        </td>
        <td className="px-3 py-3">
          <UsageCell row={p.usage.ram_mb} fmt={formatMb} cloudPct={p.cloud_share.ram_pct} />
        </td>
        <td className="px-3 py-3">
          <UsageCell row={p.usage.gigabytes} fmt={(v) => `${Math.round(v)} GB`} cloudPct={p.cloud_share.storage_pct} />
        </td>
        <td className="min-w-[150px] whitespace-nowrap px-3 py-3 text-right">
          <div className="font-medium text-color-text">{formatEur(p.cost.direct_eur)}</div>
          <div className="text-xs text-text-faint" title="direct + platform share + idle share">
            {formatEur(p.cost.fully_loaded_eur)} fully loaded
          </div>
          <div className="text-xs text-text-faint">{formatPct(p.cost.share_pct, 1)} of bill</div>
        </td>
        <td className="min-w-[130px] px-3 py-3">
          {p.budget_eur ? (
            <>
              <div className="flex items-baseline justify-between text-xs">
                <span style={{ color: ratioColor(p.budget_used_ratio) }}>{formatPct((p.budget_used_ratio ?? 0) * 100)}</span>
                <span className="text-text-faint">of {formatEur(p.budget_eur, 0)}</span>
              </div>
              <div className="mt-1">
                <ProgressBar value={quotaFill(p.budget_used_ratio ?? 0)} color={ratioColor(p.budget_used_ratio)} />
              </div>
            </>
          ) : (
            <span className="text-xs text-text-faint">no budget</span>
          )}
        </td>
        <td className="px-3 py-3">
          <div className="flex items-center gap-2">
            <StatusBadge severity={p.severity} />
            {isAdmin && (
              <button
                onClick={onEdit}
                title="Edit department & budget"
                className="rounded p-1 text-text-faint hover:text-color-text"
              >
                <Pencil className="h-3.5 w-3.5" strokeWidth={2} />
              </button>
            )}
          </div>
          {p.breaches > 0 && (
            <div className="mt-1 text-xs text-text-faint">
              {p.breaches} breach{p.breaches === 1 ? "" : "es"}
            </div>
          )}
        </td>
      </tr>
      {editing && <EditRow project={p} onDone={onDone} />}
    </>
  );
}
