# ADR-0017 — Alert routing by severity + minimal RBAC with an end-to-end audit (roadmap 4.7, 4.8)

**Status:** accepted
**Acceptance 4.7:** a critical alert reaches email within 1 minute; low severity stays dashboard-only.
**Acceptance 4.8:** at least two roles enforced; the audit log is queryable end to end for one full incident.

## 4.7 — Routing and escalation

| severity | dashboard | email | escalation (unacknowledged) |
|---|---|---|---|
| medium (and anything unknown) | yes | no | never |
| high | yes | yes | after 60 min |
| critical | yes | yes | after 15 min |

(The detectors emit `medium / high / critical`; "low" in the roadmap text is `medium`.) The emailed set and both grace periods are environment variables (`infra/.env.example`).

**Outbox, not inline SMTP.** The detectors only *insert* rows into `alert_notifications` (`services/alert_routing.py::route_event`); a dispatcher job (every 10 s) sends them. A slow or dead mail server therefore cannot stall anomaly detection, a failed send is retried with exponential backoff (`ALERT_EMAIL_MAX_ATTEMPTS`, default 6) instead of lost, and it is `failed` with the error kept when it finally gives up. Previously the SMTP call ran inside the 60 s detection pass and a failure was only logged.

**Measurable latency.** `sent_at - created_at` on the email row is the alert-to-inbox time. The clock starts when the alert exists; detection itself still runs on its 60 s tick, so "within 1 minute" is about delivery, not about how fast the metric was noticed.

**Severity changes are routed.** A medium alert that becomes critical now emails (`kind = severity_upgrade`); before, only alerts that *opened* high/critical did. One row per (episode, channel, kind, severity), enforced by a unique constraint, so repeated ticks and multiple workers never duplicate an email.

**Escalation.** An open alert with no acknowledgement, still at the severity that started the clock, gets exactly one `escalation` email to `ALERT_ESCALATION_RECIPIENT_EMAIL` (default: the normal recipient). A worsening alert restarts the clock at its new severity. `POST /anomalies/events/{id}/ack` (admin/operator) or resolving the alert stops it. Alerts that predate this change have no routing rows and are never escalated, so deploying does not page anyone for old events.

**Settings hardening.** Changing the alert recipient or sending the test email is now admin-only (it was open to any signed-in account).

## 4.8 — RBAC and the audit chain

Three roles (`users.role`), one matrix in `app/rbac.py`:

| | view | ask for info | approve / reject / execute | audit trail | ack alerts |
|---|---|---|---|---|---|
| viewer | yes | yes | no | no | no |
| operator | yes | yes | **low and medium risk** | yes | yes |
| admin | yes | yes | any risk | yes | yes |

Risk is the proposal's *effective* risk (command rating raised by the impact simulation). Anything other than `low`/`medium` — missing, or a level added later — counts as high, so an unknown risk is never easier to approve than a known one. The same check guards approve, reject and execute, and drives the `can_decide` / `can_execute` / `approver_hint` fields the UI uses.

**One chain from diagnosis to execution.** The existing hash-chained `remediation_audit_log` gains a `diagnosed` event, written with the `proposed` entry when the orchestrator records a proposal. It holds structural facts only (agents run, verdict, a SHA-256 of the question) — proposal history is readable by viewers, so the question text stays on the trace. Together with `approved / rejected / info_* / execution_*` the chain covers diagnose → propose → validate → execute.

**Querying one incident.** An incident is an agent run (`trace_id`). `GET /remediation/audit/incidents/{trace_id}` returns the four stages with who and when, the trace, the proposals, every chained entry in order, the alerts open on the affected host meanwhile (correlated by host and time — context, not part of the chain) with their routing, and the result of re-verifying the whole chain. `GET /remediation/audit/incidents` lists recent ones; `GET /remediation/audit` accepts `trace_id`, `event`, `actor`.

## Deliberately not done

- **Refused attempts are not written to the chain.** The existing design (and tests) treat "nothing was decided" as not an audit event; logging them would also let any account append rows at will. They are logged as warnings.
- **No separation of duties** (the requester may approve their own proposal). Reasonable next step once there is more than one operator.
- **Per-permission tables.** Three fixed roles are enough for now (see `models.User`).


## Addendum — detection speed and sensitivity (follow-up)

- **Detection-to-email.** Detection now runs every 30 s (was 60) and the emails it queues are sent by the same job, not on the next dispatcher tick. With a 20 s scrape the worst case from "metric crossed the line" to "critical email sent" is under a minute (the 4.7 target); the 10 s dispatcher stays as the retry and escalation safety net. The EWMA fallback's smoothing is scaled with the interval so changing it does not change how fast the fallback learns.
- **Admin-controlled sensitivity.** `detection_settings` (single row) holds a preset (`low` / `balanced` / `high` / `custom`) controlling the z-score cut-offs, the SSH failed-login floor, and a persistence requirement (an alert opens only after the reading stays abnormal for N minutes). `balanced` equals the previous hard-coded behaviour and is the default when no row exists. Changing it is admin-only; a preview replays the last 7 days of alerts against a candidate so the effect is visible before saving. Service-down alerts are not statistical and are never filtered.
- **Why persistence as well as thresholds.** Raising σ cut-offs hides real-but-modest problems; requiring a few minutes of persistence removes one-tick blips (the most common source of noise) without hiding sustained problems.
- **Incident audit page** (`/remediation-audit`) makes the per-incident API usable without tooling. The dead `/operations` sidebar entry was removed.
