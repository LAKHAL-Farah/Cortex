import { NextResponse } from "next/server";
import { authHeaders } from "@/lib/serverAuth";

const API_URL = process.env.CORTEX_API_URL!;

/** Scenario levers the browser may send. Anything else is dropped rather
 * than forwarded, so this route can't be used to reach other upstream
 * fields; value ranges are validated by the API, not here. */
const ALLOWED = [
  "extra_vms",
  "vm_vcpus",
  "vm_ram_gb",
  "vm_disk_gb",
  "vm_cpu_util_pct",
  "vm_mem_util_pct",
  "vm_disk_fill_pct",
  "start_day",
  "ramp_days",
  "workload_growth_pct_per_month",
  "compute_nodes_added",
  "storage_gb_added",
  "nodes_added_day",
  "threshold_pct",
  "horizon_days",
] as const;

/** Run one capacity what-if scenario (roadmap 4.12). Read-only upstream:
 * a simulation reads forecasts and FinOps capacity and writes nothing. */
export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as Record<string, unknown>;
  const payload: Record<string, unknown> = {};
  for (const key of ALLOWED) {
    if (body[key] !== undefined) payload[key] = body[key];
  }

  const res = await fetch(`${API_URL}/api/v1/capacity/simulate`, {
    method: "POST",
    headers: await authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}
