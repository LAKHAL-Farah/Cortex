# ADR-0017: VM right-sizing -- recommendations only (roadmap 4.10)

**Status:** Accepted
**Related code:** `services/api/app/services/rightsizing.py` (pure analysis),
`services/api/app/services/vm_utilization.py` (utilisation sources),
`services/api/app/services/rightsizing_service.py` (rates + orchestration),
`services/api/app/routers/rightsizing.py` (`GET /api/v1/rightsizing`),
`services/api/app/graph_db.py::fetch_instance_inventory`,
`services/web/components/FinopsRightSizing.tsx`,
tests `services/api/tests/test_rightsizing.py`

## Context

The FinOps work (ADR-0016) shows what projects *reserve*; it cannot say which reservations
are wasted. A VM with 8 vCPUs that peaks at 18% of one is the cloud owner's clearest
reclaim opportunity. Roadmap 4.10 asks for under-utilised VMs to be flagged with an
estimated saving, a risk level and an efficiency score -- recommendation only, no
automatic resize.

## Decisions

1. **Recommend, never act.** The router exposes `GET` only (a test asserts this), and
   nothing in the module can call Nova. A resize restarts the guest and can fail; that
   belongs to a change window or, later, the approval-gated remediation flow (ADR-0014/15).
2. **Judge on p95, guard on peak.** Classification uses p95 (an average hides the bursts a
   resize would clip). The target size must run p95 load at <= 60% CPU / <= 75% memory
   *and* keep the observed maximum under 85% of the new size. A VM with a low p95 but a
   spiky peak therefore shrinks less (the `ci-runner-03` case in the tests).
3. **Shrink only the over-provisioned dimension.** Idle CPUs with a hot buffer pool lower
   the vCPU count and leave RAM alone. A recommendation never grows a dimension.
4. **Saturated VMs are never shrunk**, and VMs with < 24 h of samples are classed
   `insufficient_data` and get no recommendation *and no efficiency score* -- a score from
   six hours of data would be false precision.
5. **Savings reuse the FinOps unit rates** (latest `finops_cloud_samples` row), so a
   savings figure is consistent with showback/chargeback. With no rates yet, savings are
   `null` ("unpriced"), not 0. They are *reclaimable run-rate*: on a fixed Hetzner bill the
   invoice only falls if the freed capacity is removed or reused. The API says so
   (`caveat`) and so do the PDFs.
6. **Risk is a transparent point score** (projected peak on the smaller size, unknown guest
   memory, short observation window), 0 -> low, 1-2 -> medium, >= 3 -> high. Every factor
   becomes a reason string shown verbatim in the UI. Purely informational notes (RAM cut
   by half, 4x vCPU step) are listed but not scored, to avoid double-counting what the
   projected-peak check already prices in.
7. **Inventory from the topology graph, utilisation from Prometheus.** The graph already
   holds every `:Instance` (flavor vCPU/RAM, project, host) from `topology_sync`, so no new
   OpenStack polling loop is introduced (ADR-0002). Real flavors from Nova are used when
   readable (and a flavor's root disk must be >= the current one -- Nova cannot shrink a
   disk); otherwise a generic size ladder is used and the output says so.
8. **No data is better than invented data.** `source=auto` means live and never falls
   back to synthetic. Without per-VM metrics the endpoint returns `available: false` with
   the reason. A deterministic **synthetic fleet** (`?source=synthetic`) exists for the
   acceptance test and demos; it is always labelled (`source: "synthetic"`, a UI banner,
   a "DEMO DATA" box in PDFs).

## Prerequisite (not yet in `infra/`)

Per-VM utilisation needs a libvirt exporter scraped by Prometheus on each compute node;
`node_exporter` only sees whole hosts. The default PromQL targets
`prometheus-libvirt-exporter` metric names and is overridable
(`RIGHTSIZING_CPU_EXPR`, `RIGHTSIZING_MEM_EXPR`, `RIGHTSIZING_VM_ID_LABEL`, see
`infra/.env.example`). **The live path has been tested against faked Prometheus results
only, not a real exporter** -- verify the metric/label names on first deployment.
Guest memory additionally requires the balloon driver / guest agent to report usage;
without it memory is `null` and only vCPUs are ever reduced.

## Consequences

- Cheap to compute (4 Prometheus subquery aggregations per expression, cached 120 s).
- Savings/risk are estimates from a single window; monthly jobs outside the window can be
  missed -- hence the confidence level and the "check for weekly/monthly jobs" note.
- Follow-ups: persisting recommendations for trend/dismissal, feeding the weekly digest,
  and a resize action behind the existing approval flow.
