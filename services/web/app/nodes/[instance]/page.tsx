"use client";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { MetricChart, useMetricHistory } from "@/components/MetricChart";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function NodeDetailPage() {
  const { instance } = useParams<{ instance: string }>();
  const decoded = decodeURIComponent(instance);
  const { data: nodes } = useSWR("/api/dashboard", fetcher, { refreshInterval: 5000 });
  const node = nodes?.find((n: any) => n.instance === decoded);
  const m = node?.metrics;

  const cpu = useMetricHistory(decoded, "cpu_percent", m?.cpu_percent);
  const mem = useMetricHistory(decoded, "memory_percent", m?.memory_percent);
  const disk = useMetricHistory(decoded, "disk_percent", m?.disk_percent);

  if (!node) return <p className="p-6 text-text-dim">Loading…</p>;

  return (
    <main className="max-w-5xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-lg font-semibold">{node.hostname}</h1>
        <p className="text-sm text-text-faint font-mono">{node.instance} · {node.role}</p>
      </div>
      {/* time-range selector (15m/1h/6h/24h) just changes the `minutes` arg passed into useMetricHistory */}
      <section className="grid gap-4">
        <div className="rounded-card border border-border bg-bg p-4">
          <div className="text-xs text-text-faint uppercase mb-2">CPU</div>
          <MetricChart data={cpu} color="var(--blue)" />
        </div>
        <div className="rounded-card border border-border bg-bg p-4">
          <div className="text-xs text-text-faint uppercase mb-2">Memory</div>
          <MetricChart data={mem} color="var(--purple)" />
        </div>
        <div className="rounded-card border border-border bg-bg p-4">
          <div className="text-xs text-text-faint uppercase mb-2">Disk</div>
          <MetricChart data={disk} color="var(--orange)" />
        </div>
      </section>
      {/* load1/5/15, uptime, disk r/w, net rx/tx as a meta-grid — same fields as NodeCard's bottom section, just larger */}
    </main>
  );
}