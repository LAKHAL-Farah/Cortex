# ADR-0016: FinOps for a private cloud -- a shared fixed bill, not a price list (roadmap 2.9)

**Status:** Accepted
**Related code:** `services/api/app/services/finops.py` (cost model),
`services/api/app/services/quota_budget_monitor.py` (capacity discovery, alerts, metering),
`services/api/app/services/finops_report.py` (showback/chargeback, PDF/CSV),
`services/api/app/routers/quotas.py` (`/overview`, `/report[.pdf|.csv]`, `/projects/{id}/settings`),
`services/api/alembic/versions/b5d7f9a1c3e5_add_finops_tables.py`,
`services/web/components/QuotaBudgetView.tsx` and `Finops*.tsx`

## Context

The Quotas & Budget page only listed alerts, and its "budget" was an estimate built
from per-vCPU / per-GB prices invented in the style of a public cloud. That is
meaningless here: RIF SAS's cloud runs on flat-rate Hetzner servers ("predictable
fixed cost", `docs/knowledge/README.md`), so the bill is identical whether the cloud is
5% or 95% full. Cortex is a monitoring tool for the *cloud owner*, whose questions are
different from a public-cloud customer's.

## Decisions

1. **Rates are derived, never invented.** `unit rate = cost pool / sellable units`.
   The monthly bill (`FINOPS_MONTHLY_COST_EUR`) is split into a *compute* pool (priced
   per vCPU and per GB RAM), a *storage* pool (per GB of Cinder) and a *platform* pool
   (controller/network/monitoring -- shared overhead). Sellable units = physical
   capacity (Nova hypervisors, Cinder backend pools) x the planned allocation ratio.
2. **Projects are charged for what they reserve**, i.e. Nova/Cinder "used" quota (flavor
   sizes, volume sizes), not CPU-seconds. A stopped VM still occupies capacity nobody
   else can have. Same basis as OpenStack's CloudKitty for flavor-based resources.
3. **Idle capacity is a first-class number.** What nobody reserved is shown as its own
   cost, and as the headline of the Overview tab.
4. **Two views per project.** *Direct* (showback) = reserved resources x rates.
   *Fully loaded* (chargeback) = direct + platform share + idle share, split pro rata to
   direct cost, so all projects together pay exactly the bill.
5. **Budget caps compare to *direct* cost.** Idle/platform shares move with what other
   projects do; alerting on them would page the wrong team. The alert message still
   says `BUDGET CAP` vs `CAPACITY CAP` explicitly (roadmap 2.9).
6. **Promised vs reserved capacity.** The sum of project quotas can exceed physical
   capacity. The Overview shows reserved *and* promised against sellable capacity, and
   counts projects with unlimited quota instead of treating them as 0.
7. **Reports are metered.** Each check upserts an hourly per-project sample holding the
   run-rate in force; a month's bill is the sum of its samples, so a later pricing or
   hardware change never rewrites history. Reports state metering coverage and, for the
   current month, a projection from the live run-rate. Exports: PDF (reportlab) and CSV.
8. **Unknown capacity is reported, not guessed.** If Nova/Cinder capacity can't be read
   (needs an admin-scoped credential) and no `FINOPS_FALLBACK_*` is set, that resource is
   *unpriced*, shown with a warning, and no budget verdict is produced from it.
9. **Department** comes from (in order): value saved in the UI, `FINOPS_PROJECT_DEPARTMENTS`,
   Keystone project tag `department:<name>`, else "Unassigned". Budget: UI value, then
   `QUOTA_PROJECT_BUDGETS_EUR`. Editing is admin-only.

## Fixed along the way

Reading another project's numbers needs the right call per service. `compute.get_limits(project_id=...)`
sends a `project_id` query parameter that Nova does not define (its selector is `tenant_id`),
and Cinder's `/limits` is bound to the token's own project. On a real cloud that can return
the credential's own project for every project. Nova now uses `tenant_id=`; Cinder usage is
read from `get_quota_set(project, usage=True)`. `openstack-sim` now mimics the real
behaviour (it previously ignored the parameter, which hid this).

## Known limits / assumptions to confirm

- The default 50/30/20 pool split, 60/40 vCPU/RAM split inside compute, and 4:1 CPU /
  1:1 RAM allocation ratios are planning assumptions -- set them to match the real
  invoice and Nova's configured ratios.
- Cost follows reservation, not consumption: it won't show a VM that is reserved but idle.
  The Overview contrasts reserved with real hypervisor load (Prometheus) to expose that gap.
- Per-project reads of *other* projects' quota need an admin-scoped `cortex-reader`.
- Cost is an internal allocation, not an invoice.
