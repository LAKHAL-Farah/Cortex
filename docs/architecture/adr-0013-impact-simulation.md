# ADR-0013: Impact simulation of proposed fixes (roadmap 4.2)

**Status:** Accepted
**Related code:** `services/api/app/services/impact_simulator.py`,
`services/api/app/graph_db.py` (`fetch_impact_context`),
`services/api/app/agents/nodes/remediation.py`,
`services/web/components/CopilotAgentPanels.tsx` (`SimulationPanel`)
**Related ADRs:** adr-0011 (the proposal it simulates), adr-0012 (containers it reasons over)

## Context

Roadmap 4.2: *a proposed fix is simulated against the Living Model and the
expected impact is shown alongside the proposal before approval.* A command's
text alone says little about its blast radius: `docker restart nova_libvirt`
looks like any other restart, but in Kolla it stops every guest on the host.

## Decisions

1. **Simulate = read the graph and apply explicit rules. Nothing is executed or
   written.** One read-only session (`fetch_impact_context`) gathers, for the
   action's host: its services and what they `SERVES`, its containers, the
   instances that `RUN_ON` it, the instances on each network it serves, the
   floating IPs behind each router it serves, the fleet's compute capacity, and
   control-plane totals. Pure rules per action kind turn that into effects.
2. **Deterministic, no LLM** (same reasoning as adr-0008/0011). Same command + same graph =
   same result, and every number shown comes from `counts`/`effects` in `raw_data`,
   so the critic's numeric grounding verifies it.
3. **Honest statuses.** `simulated`, `unmodelled` (no rule for this command; says so,
   computes no impact) and `unavailable` (graph unreadable, host unknown or not in
   the graph). Neither of the last two is ever presented as "safe". The proposal is
   shown regardless; a simulator bug costs it only its impact section.
4. **Risk only goes up.** `effective_risk = max(risk from command text, risk implied
   by the verdict)`. The plain-language risk sentence is rewritten to match.
5. **Verdicts:** `safe` (no workload touched), `caution` (workloads keep running; a
   management/control-plane function or capacity is affected), `disruptive`
   (running workloads or networks are interrupted or lost, or capacity for new
   work is gone).
6. **A gentler option is surfaced, not substituted.** When a runnable alternative
   simulates strictly lower-impact than the recommendation, the proposal says so;
   the recommendation itself is still chosen by the 4.1 rules, so it never
   silently changes with graph availability.
7. **Assumptions travel with the result** (e.g. guests live in `nova_libvirt`;
   the control plane is a single instance).

## Modelled today

Container restart/stop (nova_compute, nova_libvirt, neutron L3/DHCP/OVS agents,
cinder_volume, rabbitmq/mariadb/keystone/haproxy/… control plane), compute service
enable/disable (with remaining-capacity check), instance reboot/rebuild/delete/
reset-state/migrate, host reboot, docker prune (names the stopped containers it
would delete), node_exporter restart, log housekeeping. Anything else is `unmodelled`.

## Out of scope (later roadmap items)

Approval UI, execution and audit (Phase 6); re-ranking the recommendation by
simulated impact; modelling inside guests.
