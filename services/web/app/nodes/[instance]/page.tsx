"use client";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useMetricHistory } from "@/components/MetricChart";
import PlotlyChart from "@/components/PlotlyChart";
import { useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

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

  if (!node) return <p className="p-6 text-text-dim">Loading…</p>;

  const latest = (arr: any[]) => (arr && arr.length ? arr[arr.length - 1].v : undefined);

  return (
    <main className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex flex-col gap-2">
        <div className="text-sm uppercase tracking-[0.22em] text-text-faint">Node</div>
        <h1 className="text-3xl font-semibold text-color-text">{node.hostname}</h1>
        <div className="text-sm text-text-faint">{node.instance} · {node.role}</div>
      </div>

      <div className="mb-6 flex flex-col gap-4 rounded-[28px] border border-color-border/70 bg-white/80 p-6 shadow-sm backdrop-blur-sm sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="text-sm uppercase tracking-[0.28em] text-text-faint">Node details</div>
          <h1 className="mt-3 text-4xl font-semibold text-color-text">{node.hostname}</h1>
          <div className="mt-2 text-sm text-text-dim">{node.instance} · {node.role}</div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded-full px-4 py-2 text-sm font-semibold ${m?.status === 'up' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
            {m?.status === 'up' ? 'Online' : 'Offline'}
          </span>
          <span className="rounded-full bg-slate-100 px-4 py-2 text-sm text-text-dim">{m?.health ?? 'Unknown'}</span>
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <aside className="space-y-5">
          <Card className="p-5">
            <div className="space-y-5">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-text-faint">Overview</div>
                <div className="mt-3 text-2xl font-semibold text-color-text">{node.hostname}</div>
                <div className="text-sm text-text-faint">{node.instance}</div>
              </div>

              <div className="grid gap-3">
                <div className="rounded-[24px] bg-bg p-4 shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
                  <div className="text-xs uppercase tracking-[0.18em] text-text-faint">Status</div>
                  <div className="mt-2 text-base font-semibold text-color-text">{m?.status === 'up' ? 'Online' : 'Offline'}</div>
                </div>
                <div className="rounded-[24px] bg-bg p-4 shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
                  <div className="text-xs uppercase tracking-[0.18em] text-text-faint">Health</div>
                  <div className="mt-2 text-base font-semibold text-color-text">{m?.health ?? 'Unknown'}</div>
                </div>
                <div className="rounded-[24px] bg-bg p-4 shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
                  <div className="text-xs uppercase tracking-[0.18em] text-text-faint">Last seen</div>
                  <div className="mt-2 text-base font-semibold text-color-text">{node.last_seen ?? 'Unknown'}</div>
                </div>
              </div>
            </div>
          </Card>

          <Card className="p-5">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div className="text-sm font-semibold text-color-text">Quick metrics</div>
                <span className="text-xs text-text-faint">{minutes} minutes</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {[
                  { label: 'CPU', value: m?.cpu_percent, unit: '%', tone: 'bg-blue-bg' },
                  { label: 'Memory', value: m?.memory_percent, unit: '%', tone: 'bg-purple-bg' },
                  { label: 'Disk', value: m?.disk_percent, unit: '%', tone: 'bg-orange-bg' },
                  { label: 'Uptime', value: m?.uptime, unit: '', tone: 'bg-gray-bg' },
                ].map((item) => (
                  <div key={item.label} className="rounded-[24px] bg-bg p-4 shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
                    <div className="text-xs uppercase tracking-[0.18em] text-text-faint">{item.label}</div>
                    <div className="mt-2 text-2xl font-semibold text-color-text">{item.value ?? '—'}{item.unit}</div>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </aside>

        <section className="space-y-5">
          <Card className="p-5">
            <div className="mb-5 flex flex-col gap-4 border-b border-color-border/70 pb-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-xs uppercase tracking-[0.18em] text-text-faint">Historical metrics</div>
                <div className="mt-2 text-xl font-semibold text-color-text">Performance overview</div>
              </div>
              <div className="flex flex-wrap gap-2">
                {[
                  { label: '15m', mins: 15 },
                  { label: '1h', mins: 60 },
                  { label: '6h', mins: 360 },
                  { label: '24h', mins: 1440 },
                  { label: '7d', mins: 10080 },
                ].map((r) => (
                  <button
                    key={r.mins}
                    onClick={() => setMinutes(r.mins)}
                    className={`rounded-full px-4 py-2 text-sm font-semibold transition ${minutes === r.mins ? 'bg-blue text-white shadow-sm' : 'border border-color-border bg-bg text-color-text hover:bg-bg-hover'}`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid gap-4">
              <div className="rounded-[28px] bg-bg p-5 shadow-[0_10px_32px_rgba(15,23,42,0.04)]">
                <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <div className="text-xs uppercase tracking-[0.18em] text-text-faint">CPU Usage</div>
                    <div className="mt-2 text-3xl font-semibold text-color-text">{latest(cpu) !== undefined ? `${latest(cpu)}%` : (m?.cpu_percent ?? '—')}</div>
                  </div>
                  <div className="text-xs text-text-faint">Last update: {node.last_seen ?? '—'}</div>
                </div>
                <PlotlyChart data={cpu} color="rgb(11,110,153)" height={280} />
              </div>

              <div className="rounded-[28px] bg-bg p-5 shadow-[0_10px_32px_rgba(15,23,42,0.04)]">
                <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <div className="text-xs uppercase tracking-[0.18em] text-text-faint">Memory Usage</div>
                    <div className="mt-2 text-3xl font-semibold text-color-text">{latest(mem) !== undefined ? `${latest(mem)}%` : (m?.memory_percent ?? '—')}</div>
                  </div>
                  <div className="text-xs text-text-faint">Last update: {node.last_seen ?? '—'}</div>
                </div>
                <PlotlyChart data={mem} color="rgb(107,33,168)" height={280} />
              </div>

              <div className="rounded-[28px] bg-bg p-5 shadow-[0_10px_32px_rgba(15,23,42,0.04)]">
                <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <div className="text-xs uppercase tracking-[0.18em] text-text-faint">Disk Usage</div>
                    <div className="mt-2 text-3xl font-semibold text-color-text">{latest(disk) !== undefined ? `${latest(disk)}%` : (m?.disk_percent ?? '—')}</div>
                  </div>
                  <div className="text-xs text-text-faint">Last update: {node.last_seen ?? '—'}</div>
                </div>
                <PlotlyChart data={disk} color="rgb(249,115,22)" height={280} />
              </div>
            </div>
          </Card>
        </section>
      </div>
    </main>
  );
}