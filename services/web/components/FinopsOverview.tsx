"use client";

import useSWR from "swr";
import { AlertTriangle, Banknote, Cpu, HardDrive, Layers, MemoryStick, PiggyBank, Server, Info } from "lucide-react";
import type { FinopsCapacityRow, FinopsCloud, FinopsOverview as Overview, FinopsProject } from "@/lib/types";
import { formatCount, formatEur, formatMb, formatPct, jsonFetcher, projectColor } from "@/lib/quotas";
import { Card } from "./ui/Card";

export function useFinopsOverview() {
  return useSWR<Overview>("/api/quotas/overview", jsonFetcher<Overview>, {
    refreshInterval: 15000,
    keepPreviousData: true,
  });
}

function Kpi({
  label,
  value,
  sub,
  color,
  icon: Icon,
}: {
  label: string;
  value: string;
  sub: string;
  color: string;
  icon: typeof Banknote;
}) {
  return (
    <Card className="flex items-start gap-3">
      <span
        className="grid h-9 w-9 flex-shrink-0 place-items-center rounded-[var(--radius-control)]"
        style={{ background: `color-mix(in srgb, ${color} 14%, transparent)` }}
      >
        <Icon className="h-4 w-4" style={{ color }} strokeWidth={1.75} />
      </span>
      <div className="min-w-0">
        <div className="stat-figure text-[22px] text-color-text">{value}</div>
        <div className="text-xs font-medium text-color-text">{label}</div>
        <div className="mt-0.5 text-xs text-text-faint">{sub}</div>
      </div>
    </Card>
  );
}

/** "Where the monthly bill goes": one bar = the whole fixed bill. Segments
 * are each project's own reserved resources, then shared platform overhead,
 * then idle capacity -- so the owner sees at a glance how much of what they
 * pay for actually produces anything. */
function BillBreakdown({ cloud, projects }: { cloud: FinopsCloud; projects: FinopsProject[] }) {
  const total = cloud.monthly_cost_eur || 1;
  const segments = [
    ...projects.map((p, i) => ({
      key: p.project_id,
      label: p.project_name,
      value: p.cost.direct_eur,
      color: projectColor(i),
      hatch: false,
    })),
    { key: "__platform", label: "Shared platform nodes", value: cloud.pools.platform_eur, color: "var(--text-faint)", hatch: false },
    { key: "__idle", label: "Idle capacity", value: cloud.cost.idle_eur, color: "var(--warn)", hatch: true },
  ].filter((s) => s.value > 0.005);

  return (
    <Card>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-color-text">Where the monthly bill goes</h2>
        <span className="text-xs text-text-faint">
          {formatEur(cloud.monthly_cost_eur, 0)}/month fixed — the same whether the cloud is empty or full
        </span>
      </div>
      <div className="mt-3 flex h-6 overflow-hidden rounded-[var(--radius-control)]" style={{ background: "var(--border-soft)" }}>
        {segments.map((s) => (
          <div
            key={s.key}
            title={`${s.label}: ${formatEur(s.value)}`}
            style={{
              width: `${(s.value / total) * 100}%`,
              background: s.hatch
                ? `repeating-linear-gradient(45deg, ${s.color} 0 4px, color-mix(in srgb, ${s.color} 40%, transparent) 4px 8px)`
                : s.color,
            }}
          />
        ))}
      </div>
      <ul className="mt-3 grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2 lg:grid-cols-3">
        {segments.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 flex-shrink-0 rounded-sm" style={{ background: s.color }} />
            <span className="min-w-0 flex-1 truncate text-text-dim">{s.label}</span>
            <span className="text-color-text">{formatEur(s.value)}</span>
            <span className="w-10 text-right text-text-faint">{formatPct((s.value / total) * 100)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-text-faint">
        Project segments are what each project has <em>reserved</em> (VM flavors + volumes) at the rates below.
        Platform nodes (controller, network, monitoring) are shared overhead; idle capacity is hardware nobody has
        reserved yet — it is spread across projects pro rata in the chargeback report.
      </p>
    </Card>
  );
}

function CapacityRow({
  icon: Icon,
  label,
  row,
  fmt,
  unit,
}: {
  icon: typeof Cpu;
  label: string;
  row: FinopsCapacityRow;
  fmt: (v: number | null) => string;
  unit: string;
}) {
  if (!row.sellable) {
    return (
      <div className="flex items-center gap-3 text-sm text-text-faint">
        <Icon className="h-4 w-4" strokeWidth={1.75} />
        <span className="w-24">{label}</span>
        <span>Capacity unknown — see the warning above.</span>
      </div>
    );
  }
  const allocated = Math.min(row.allocated_pct ?? 0, 100);
  const committedPct = row.committed_pct ?? 0;
  const overPromised = committedPct > 100 || row.unlimited_projects > 0;
  const markerLeft = Math.min(committedPct, 100);
  return (
    <div>
      <div className="flex items-center gap-2 text-sm">
        <Icon className="h-4 w-4 text-text-faint" strokeWidth={1.75} />
        <span className="font-medium text-color-text">{label}</span>
        <span className="ml-auto text-xs text-text-faint">
          {fmt(row.allocated)} reserved of {fmt(row.sellable)} {unit}
          {row.physical !== null && row.sellable !== row.physical && <> (from {fmt(row.physical)} physical)</>}
        </span>
      </div>
      <div className="relative mt-1.5 h-3 rounded-full" style={{ background: "var(--border-soft)" }}>
        <div
          className="h-full rounded-full transition-[width] duration-700"
          style={{ width: `${allocated}%`, background: allocated >= 90 ? "var(--warn)" : "var(--accent)" }}
        />
        {row.committed > 0 && (
          <div
            title={`Quotas promise ${fmt(row.committed)} ${unit}`}
            className="absolute -top-1 h-5 w-0.5"
            style={{ left: `calc(${markerLeft}% - 1px)`, background: overPromised ? "var(--crit)" : "var(--text-dim)" }}
          />
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-text-faint">
        <span>{formatPct(row.allocated_pct)} reserved</span>
        <span style={{ color: overPromised ? "var(--crit)" : undefined }}>
          Quotas promise {fmt(row.committed)} ({formatPct(row.committed_pct)})
          {committedPct > 100 && " — more than the cloud can deliver if everyone uses their quota"}
        </span>
        {row.unlimited_projects > 0 && (
          <span style={{ color: "var(--warn)" }}>
            + {row.unlimited_projects} project{row.unlimited_projects === 1 ? "" : "s"} with unlimited quota
          </span>
        )}
      </div>
    </div>
  );
}

function ReservedVsReal({ cloud }: { cloud: FinopsCloud }) {
  const util = cloud.physical_utilization;
  const reservedCpu = cloud.capacity.vcpus.allocated_pct;
  const reservedRam = cloud.capacity.ram_mb.allocated_pct;
  if (!util) {
    return (
      <p className="text-xs text-text-faint">
        Live compute-node load unavailable (Prometheus not reachable) — can&apos;t compare reserved vs actually used.
      </p>
    );
  }
  const rows = [
    { label: "CPU", reserved: reservedCpu, real: util.cpu_percent },
    { label: "Memory", reserved: reservedRam, real: util.memory_percent },
  ];
  const wasteful = rows.some((r) => r.reserved !== null && r.reserved - r.real >= 30);
  return (
    <div>
      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[64px_1fr_auto] items-center gap-3 text-xs">
            <span className="text-text-dim">{r.label}</span>
            <div className="space-y-1">
              <div className="h-1.5 rounded-full" style={{ background: "var(--border-soft)" }}>
                <div className="h-full rounded-full" style={{ width: `${Math.min(r.reserved ?? 0, 100)}%`, background: "var(--accent)" }} />
              </div>
              <div className="h-1.5 rounded-full" style={{ background: "var(--border-soft)" }}>
                <div className="h-full rounded-full" style={{ width: `${Math.min(r.real, 100)}%`, background: "var(--chart-3)" }} />
              </div>
            </div>
            <span className="text-text-faint">
              {formatPct(r.reserved)} reserved · {formatPct(r.real)} busy
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-text-faint">
        Reserved = share of sellable capacity held by project VMs (what you pay for). Busy = what the{" "}
        {util.compute_nodes} compute node{util.compute_nodes === 1 ? "" : "s"} are really doing now.
        {wasteful && (
          <span style={{ color: "var(--warn)" }}>
            {" "}
            Large gap: projects hold much more than they use — candidates for right-sizing or quota reclaim.
          </span>
        )}
      </p>
    </div>
  );
}

function RateCard({ cloud }: { cloud: FinopsCloud }) {
  const r = cloud.rates;
  const cell = (label: string, value: number | null, unit: string) => (
    <div className="rounded-[var(--radius-control)] border p-3" style={{ borderColor: "var(--border-soft)" }}>
      <div className="text-xs text-text-faint">{label}</div>
      <div className="stat-figure text-lg text-color-text">{value === null ? "unknown" : formatEur(value, 3)}</div>
      <div className="text-xs text-text-faint">{unit}</div>
    </div>
  );
  return (
    <Card>
      <h2 className="text-sm font-semibold text-color-text">Derived unit cost</h2>
      <p className="mt-1 text-xs text-text-faint">
        Not a price list: each rate is the cost pool for that resource divided by the units the cloud can sell
        ({cloud.allocation_ratios.cpu}:1 vCPU and {cloud.allocation_ratios.ram}:1 RAM allocation ratio on{" "}
        {cloud.compute_nodes} compute node{cloud.compute_nodes === 1 ? "" : "s"}).
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {cell("vCPU", r.vcpu_month_eur, "per vCPU / month")}
        {cell("RAM", r.ram_gb_month_eur, "per GB / month")}
        {cell("Block storage", r.storage_gb_month_eur, "per GB / month")}
        {cell("Floating IP", r.floating_ip_month_eur, "per IP / month (pass-through)")}
      </div>
      <div className="mt-3 grid gap-1 text-xs text-text-faint sm:grid-cols-3">
        <span>Compute pool: <b className="text-text-dim">{formatEur(cloud.pools.compute_eur, 0)}</b></span>
        <span>Storage pool: <b className="text-text-dim">{formatEur(cloud.pools.storage_eur, 0)}</b></span>
        <span>Platform pool: <b className="text-text-dim">{formatEur(cloud.pools.platform_eur, 0)}</b> (shared)</span>
      </div>
    </Card>
  );
}

export default function FinopsOverview() {
  const { data, error, isLoading } = useFinopsOverview();

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
    return (
      <div className="flex flex-col items-center gap-2 p-10 text-center">
        <PiggyBank className="h-5 w-5 text-text-faint" strokeWidth={1.75} />
        <p className="max-w-md text-sm text-text-faint">
          No metering data yet. Cortex records project usage and cost every few minutes — press{" "}
          <b>Check now</b> to take the first reading from OpenStack.
        </p>
      </div>
    );
  }

  const { cloud, projects } = data;
  const reserved = cloud.cost.utilization_pct;
  const idleShare = cloud.monthly_cost_eur ? (cloud.cost.idle_eur / cloud.monthly_cost_eur) * 100 : 0;

  return (
    <div className="space-y-4">
      {(cloud.warnings.length > 0 || cloud.oversold) && (
        <div className="space-y-2">
          {cloud.oversold && (
            <div className="panel flex items-start gap-2 p-3 text-sm" style={{ borderColor: "var(--warn)" }}>
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
              <span className="text-text-dim">
                Projects have reserved more than the planned sellable capacity — the cloud is oversold at the
                configured {cloud.allocation_ratios.cpu}:1 / {cloud.allocation_ratios.ram}:1 ratios. Idle cost shows as €0
                and unit rates understate the real cost per reserved unit.
              </span>
            </div>
          )}
          {cloud.warnings.map((w) => (
            <div key={w} className="panel flex items-start gap-2 p-3 text-sm" style={{ borderColor: "var(--warn)" }}>
              <Info className="mt-0.5 h-4 w-4 flex-shrink-0" style={{ color: "var(--warn)" }} strokeWidth={2} />
              <span className="text-text-dim">{w}</span>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Monthly cloud bill" value={formatEur(cloud.monthly_cost_eur, 0)} sub="fixed — servers & network" color="var(--chart-2)" icon={Banknote} />
        <Kpi
          label="Reserved by projects"
          value={formatEur(cloud.cost.allocated_eur, 0)}
          sub={reserved === null ? "capacity unknown" : `${formatPct(reserved)} of compute + storage pools`}
          color="var(--chart-3)"
          icon={Layers}
        />
        <Kpi
          label="Idle capacity"
          value={formatEur(cloud.cost.idle_eur, 0)}
          sub={`${formatPct(idleShare)} of the bill buys nothing yet`}
          color="var(--warn)"
          icon={PiggyBank}
        />
        <Kpi label="Shared platform" value={formatEur(cloud.cost.platform_eur, 0)} sub="controller · network · monitoring" color="var(--text-faint)" icon={Server} />
      </div>

      <BillBreakdown cloud={cloud} projects={projects} />

      <Card>
        <h2 className="text-sm font-semibold text-color-text">Capacity: reserved vs promised</h2>
        <p className="mb-3 mt-1 text-xs text-text-faint">
          The bar is what the cloud can sell. The fill is what projects have reserved now; the tick is the sum of all
          project quotas — what the cloud has <em>promised</em>. Promising more than 100% is fine only if you are sure
          not everyone will use their quota at once.
        </p>
        <div className="space-y-4">
          <CapacityRow icon={Cpu} label="vCPUs" row={cloud.capacity.vcpus} fmt={(v) => formatCount(v)} unit="vCPUs" />
          <CapacityRow icon={MemoryStick} label="RAM" row={cloud.capacity.ram_mb} fmt={(v) => formatMb(v)} unit="" />
          <CapacityRow
            icon={HardDrive}
            label="Block storage"
            row={cloud.capacity.gigabytes}
            fmt={(v) => (v === null ? "—" : `${Math.round(v)} GB`)}
            unit=""
          />
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="text-sm font-semibold text-color-text">Reserved vs actually used</h2>
          <p className="mb-3 mt-1 text-xs text-text-faint">Top bar: reserved by project VMs. Bottom bar: real load on the hypervisors.</p>
          <ReservedVsReal cloud={cloud} />
        </Card>
        <RateCard cloud={cloud} />
      </div>
    </div>
  );
}
