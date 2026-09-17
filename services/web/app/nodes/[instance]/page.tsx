"use client";
import { useParams } from "next/navigation";
import useSWR from "swr";
import Link from "next/link";
import { useMetricHistory } from "@/components/MetricChart";
import PlotlyChart from "@/components/PlotlyChart";
import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/Card";
import { ArrowLeft, Server, TrendingUp, ShieldAlert, ShieldQuestion, ShieldCheck, ChevronRight } from "lucide-react";
import type { SecurityFinding } from "@/lib/types";
import { overallSecurityTone } from "@/lib/securityStatus";
import SecurityScopeTag from "@/components/SecurityScopeTag";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const RANGES = [
  { label: "15m", mins: 15 },
  { label: "1h", mins: 60 },
  { label: "6h", mins: 360 },
  { label: "24h", mins: 1440 },
  { label: "7d", mins: 10080 },
];

const HEALTH_COLOR: Record<string, string> = {
  healthy: "var(--ok)",
  warning: "var(--warn)",
  critical: "var(--crit)",
};
const HEALTH_SOFT: Record<string, string> = {
  healthy: "var(--ok-soft)",
  warning: "var(--warn-soft)",
  critical: "var(--crit-soft)",
};

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 text-sm">
      <span className="text-text-faint">{label}</span>
      <span className="stat-figure text-color-text">{value ?? "—"}</span>
    </div>
  );
}

function MetricPanel({
  title,
  latestValue,
  data,
  color,
  lastSeen,
}: {
  title: string;
  latestValue: React.ReactNode;
  data: any[];
  color: string;
  lastSeen?: string;
}) {
  return (
    <div className="panel p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="eyebrow">{title}</div>
          <div className="stat-figure mt-1 text-2xl text-color-text">{latestValue}</div>
        </div>
        <div className="text-xs text-text-faint">Updated {lastSeen ?? "—"}</div>
      </div>
      <PlotlyChart data={data} color={color} height={200} />
    </div>
  );
}

export default function NodeDetailPage() {
  const { instance } = useParams<{ instance: string }>();
  const decoded = decodeURIComponent(instance);
  const [minutes, setMinutes] = useState<number>(60);
  const { data: nodes } = useSWR("/api/dashboard", fetcher, { refreshInterval: 5000 });
  const node = nodes?.find((n: any) =>
    [n.instance, n.id, n.hostname, n.ip_address].some((value) => value === decoded)
  );
  const m = node?.metrics;

  useEffect(() => {
    try {
      const key = `cortex:range:${decoded}`;
      const saved = localStorage.getItem(key);
      if (saved) setMinutes(parseInt(saved, 10));
    } catch (e) {}
  }, [decoded]);

  useEffect(() => {
    try {
      const key = `cortex:range:${decoded}`;
      localStorage.setItem(key, String(minutes));
    } catch (e) {}
  }, [decoded, minutes]);

  const cpu = useMetricHistory(decoded, "cpu_percent", m?.cpu_percent, minutes);
  const mem = useMetricHistory(decoded, "memory_percent", m?.memory_percent, minutes);
  const disk = useMetricHistory(decoded, "disk_percent", m?.disk_percent, minutes);

  // Phase Sec-6 cross-linking: "Each node's existing /nodes/[instance]
  // detail page should gain a Security posture section linking into the
  // relevant /security/* sub-page pre-filtered to that host -- the
  // dashboard is a new front door, not a second place this data lives."
  // Reuses the exact same GET /api/security/findings every /security/*
  // page already polls, rather than a second per-node endpoint -- this
  // node's own row is just the SecurityFinding whose hostname matches.
  const { data: secData } = useSWR<{ findings: SecurityFinding[] }>("/api/security/findings", fetcher, {
    refreshInterval: 15000,
  });
  const securityFinding = useMemo(
    () => secData?.findings.find((f) => f.hostname === node?.hostname),
    [secData, node?.hostname],
  );

  if (!node) return <p className="p-6 text-sm text-text-faint">Loading…</p>;

  const latest = (arr: any[]) => (arr && arr.length ? arr[arr.length - 1].v : undefined);
  const health = m?.health ?? "healthy";

  return (
    <main className="grid gap-4">
      <Link href="/nodes" className="inline-flex items-center gap-1.5 text-sm text-text-faint hover:text-text-dim">
        <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
        Nodes
      </Link>

      <div className="panel flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)]" style={{ background: "var(--canvas)" }}>
            <Server className="h-4.5 w-4.5 text-text-dim" strokeWidth={1.75} />
          </div>
          <div>
            <h1 className="font-display text-lg font-semibold text-color-text">{node.hostname}</h1>
            <div className="text-sm text-text-faint">{node.ip_address}:{node.exporter_port} · {node.role}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
            style={{
              color: m?.status === "up" ? "var(--ok)" : "var(--crit)",
              background: m?.status === "up" ? "var(--ok-soft)" : "var(--crit-soft)",
            }}
          >
            <span className="status-dot" style={{ background: m?.status === "up" ? "var(--ok)" : "var(--crit)" }} />
            {m?.status === "up" ? "Online" : "Offline"}
          </span>
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
            style={{ color: HEALTH_COLOR[health], background: HEALTH_SOFT[health] }}
          >
            {health}
          </span>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <aside className="space-y-4">
          <div className="panel p-4">
            <div className="eyebrow mb-1">Details</div>
            <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
              <InfoRow label="Last seen" value={node.last_seen} />
              <InfoRow label="Load (1/5/15)" value={m ? `${m.load1} / ${m.load5} / ${m.load15}` : undefined} />
              <InfoRow label="Uptime" value={m?.uptime} />
              <InfoRow label="Disk R/W" value={m ? `${m.disk_read} · ${m.disk_write}` : undefined} />
              <InfoRow label="Network RX/TX" value={m ? `${m.network_rx} · ${m.network_tx}` : undefined} />
              <InfoRow label="Active processes" value={m?.procs_running} />
              <InfoRow label="Blocked processes" value={m?.procs_blocked} />
              <InfoRow label="Swap" value={m?.swap_percent !== undefined ? `${m.swap_percent}%` : undefined} />
            </div>
          </div>

          <div className="panel p-4">
            <div className="eyebrow mb-3">Quick metrics</div>
            <div className="grid grid-cols-2 gap-2.5">
              <div className="rounded-[var(--radius-control)] p-3" style={{ background: "var(--canvas)" }}>
                <div className="text-[11px] text-text-faint">CPU</div>
                <div className="stat-figure mt-1 text-lg text-color-text">{m?.cpu_percent ?? "—"}%</div>
              </div>
              <div className="rounded-[var(--radius-control)] p-3" style={{ background: "var(--canvas)" }}>
                <div className="text-[11px] text-text-faint">Memory</div>
                <div className="stat-figure mt-1 text-lg text-color-text">{m?.memory_percent ?? "—"}%</div>
              </div>
              <div className="rounded-[var(--radius-control)] p-3" style={{ background: "var(--canvas)" }}>
                <div className="text-[11px] text-text-faint">Disk</div>
                <div className="stat-figure mt-1 text-lg text-color-text">{m?.disk_percent ?? "—"}%</div>
              </div>
              <div className="rounded-[var(--radius-control)] p-3" style={{ background: "var(--canvas)" }}>
                <div className="text-[11px] text-text-faint">Uptime</div>
                <div className="stat-figure mt-1 text-lg text-color-text">{m?.uptime ?? "—"}</div>
              </div>
            </div>
          </div>

          <SecurityPosturePanel hostname={node.hostname} finding={securityFinding} />
        </aside>

        <section className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="eyebrow">Historical metrics</div>
            <div className="flex items-center gap-2">
              <Link
                href={`/baselines?host=${encodeURIComponent(node.hostname)}&metric=cpu_usage`}
                className="inline-flex items-center gap-1.5 rounded-[var(--radius-control)] px-2.5 py-1 text-xs font-medium text-text-dim transition-colors hover:bg-[var(--canvas)]"
                style={{ border: "1px solid var(--border)" }}
              >
                <TrendingUp className="h-3.5 w-3.5" strokeWidth={2} />
                View baseline
              </Link>
              <div className="inline-flex rounded-[var(--radius-control)] p-0.5" style={{ border: "1px solid var(--border)" }}>
                {RANGES.map((r) => (
                  <button
                    key={r.mins}
                    onClick={() => setMinutes(r.mins)}
                    className="rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors"
                    style={{
                      background: minutes === r.mins ? "var(--accent)" : "transparent",
                      color: minutes === r.mins ? "#fff" : "var(--text-dim)",
                    }}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <MetricPanel
            title="CPU usage"
            latestValue={`${latest(cpu) !== undefined ? latest(cpu) : m?.cpu_percent ?? "—"}%`}
            data={cpu}
            color="var(--chart-1)"
            lastSeen={node.last_seen}
          />
          <MetricPanel
            title="Memory usage"
            latestValue={`${latest(mem) !== undefined ? latest(mem) : m?.memory_percent ?? "—"}%`}
            data={mem}
            color="var(--chart-4)"
            lastSeen={node.last_seen}
          />
          <MetricPanel
            title="Disk usage"
            latestValue={`${latest(disk) !== undefined ? latest(disk) : m?.disk_percent ?? "—"}%`}
            data={disk}
            color="var(--chart-5)"
            lastSeen={node.last_seen}
          />
        </section>
      </div>
    </main>
  );
}

// Phase Sec-6 cross-linking: the dashboard is a new front door, not a
// second place this data lives -- so this panel never renders its own
// security data, only a status pill (same overallSecurityTone every
// /security page already uses) plus links out to the real sub-pages.
// Security groups already has a real per-host route
// (/security/security-groups/[hostname]); auth activity, exposed ports,
// vulnerabilities, and kernel signals fetch the fleet-wide findings
// client-side and accept ?host= to pre-filter (see
// lib/securityStatus.ts's filterFindingsByHost). Keystone tokens is
// Identity-scoped and fleet-wide (no host to filter by, see that page's
// own docstring), so it's deliberately left off this per-node list.
function SecurityPosturePanel({ hostname, finding }: { hostname: string; finding: SecurityFinding | undefined }) {
  const links = [
    { label: "Security groups", href: `/security/security-groups/${encodeURIComponent(hostname)}` },
    { label: "Auth activity", href: `/security/auth-activity?host=${encodeURIComponent(hostname)}` },
    { label: "Exposed ports", href: `/security/exposed-ports?host=${encodeURIComponent(hostname)}` },
    { label: "Vulnerabilities", href: `/security/vulnerabilities?host=${encodeURIComponent(hostname)}` },
    { label: "Kernel signals", href: `/security/kernel-signals?host=${encodeURIComponent(hostname)}` },
  ];

  const tone = finding ? overallSecurityTone(finding.raw_data) : null;

  return (
    <div className="panel p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="eyebrow">Security posture</div>
        <SecurityScopeTag scope="node" />
      </div>

      {!finding ? (
        <div className="flex items-center gap-2 text-xs text-text-faint">
          <ShieldQuestion className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
          Not scanned yet, or not a monitored security host.
        </div>
      ) : (
        <div
          className="mb-3 flex items-center gap-2 rounded-[var(--radius-control)] p-2.5 text-sm"
          style={{ background: tone!.soft, color: tone!.color }}
        >
          {tone!.label === "Clean" ? (
            <ShieldCheck className="h-4 w-4 shrink-0" strokeWidth={2} />
          ) : (
            <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={2} />
          )}
          <span className="font-medium">{tone!.label}</span>
        </div>
      )}

      <div className="divide-y" style={{ borderColor: "var(--border-soft)" }}>
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="flex items-center justify-between gap-2 py-2 text-sm text-text-dim hover:text-color-text"
          >
            {l.label}
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-faint" strokeWidth={2} />
          </Link>
        ))}
      </div>
    </div>
  );
}
