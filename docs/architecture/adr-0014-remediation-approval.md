# ADR-0014: Approve / reject / ask-for-more-info, every decision logged (roadmap 4.3)

**Status:** Accepted
**Related code:** `services/api/app/services/remediation_approval.py`,
`services/api/app/routers/remediation.py`,
`services/api/app/routers/agents.py` (`_record_fix_proposal`),
`services/api/app/models.py` (`RemediationProposal`, `RemediationAuditEntry`),
`services/api/alembic/versions/e7a1c3d5f9b2_add_remediation_approval.py`,
`services/web/components/RemediationApproval.tsx`
**Related ADRs:** adr-0011 (the proposal being decided on), adr-0013 (the simulation shown beside it)

## Context

Roadmap 4.3: *a person can approve, reject, or ask for more information about a
proposed fix, and an audit trail entry is created, timestamped, for each
decision.* 4.1 produces proposals and 4.2 shows their expected impact; both
say "needs your approval" but nothing could be approved. The project doc
(§7.11) also requires the audit journal to be mandatory and tamper-evident
for the whole chain diagnosis → proposal → validation → execution.

## Decisions

1. **Decisions refer to a server-side snapshot, never to what the browser
   sends.** `POST /orchestrate` stores the `fix_proposal` it just produced
   (`remediation_proposals`, one row per turn and `proposal_id`) and returns a
   handle, `fix_proposal.approval.id`. The decision request carries only
   `decision` and `comment`. The audit entry copies the exact command, host,
   risk and simulation verdict from the snapshot, plus a SHA-256 digest of the
   whole proposal. `proposal_id` is deterministic per (symptom, host,
   command), so the same incident on two days gives two rows: yesterday's
   approval can never count for today's proposal.
2. **Three decisions, one final state each.** `approve` and `reject` are final
   (a second attempt is a 409, never a contradicting entry). `ask_more_info`
   keeps the proposal open (`info_requested`); it can be repeated and followed
   by approve or reject.
3. **Who may decide.** Approve and reject need the `admin` role. Asking for
   more information is open to any signed-in account, so wanting to understand
   a proposal is not a privilege. This settles the doc's §13 open question for
   now with the two roles that exist; an "operators may approve low-risk fixes"
   tier belongs with the trust ladder (Phase 9). A refused attempt is logged
   to the application log, not the audit trail: nothing was decided.
4. **A reason where silence would leave a gap:** every rejection, every request
   for more info, and approval of anything with effective risk `high` (the
   proposal already tells people to get a second pair of eyes on those).
5. **Status change and audit entry commit together,** under a row lock on the
   proposal, so two simultaneous decisions yield one winner and one 409.
6. **The audit log is append-only in three layers.**
   - A Postgres trigger rejects UPDATE, DELETE and TRUNCATE on
     `remediation_audit_log`.
   - Actor columns are snapshots (username, role) and `actor_user_id` is
     deliberately not a foreign key: `ON DELETE SET NULL` would itself be an
     UPDATE of an audit row when an account is removed.
   - Every entry carries `prev_hash`/`entry_hash`, a SHA-256 chain over the
     whole table in `id` order. A superuser or restored backup editing rows
     behind the trigger shows up as a break in `GET /audit/verify`. Appends
     take a transaction-scoped advisory lock so the chain stays linear.
7. **A `proposed` entry is logged too,** so the trail reads proposal → decision
   (→ execution, later) as one sequence rather than starting at the decision.
8. **If recording a proposal fails, the answer still arrives, without
   approval controls.** The UI then says the proposal was not recorded. Nothing
   can be approved that was not first recorded.
9. **Approval executes nothing.** `approved` only says a person signed off.
   The UI says so next to the status. (Execution, behind a separate explicit
   click, is adr-0015.)

## Consequences / limits

- The hash chain proves integrity of what is in the table, not that the table
  is complete: someone with database superuser rights could also truncate the
  tail and rebuild hashes. Shipping entries to external storage is the answer
  to that, and is not done here.
- Asking for more info logs the question. *(Answered by Cortex from the stored
  proposal since adr-0015; a reply from a colleague is still a follow-up.)*
- No global audit page in the UI. `GET /api/v1/remediation/audit` (admin-only)
  serves it when one is wanted; the per-proposal history is on the card.

## Out of scope (later roadmap items)

- Executing an approved fix *(done in adr-0015)*. When it exists it must read `status = 'approved'`
  from `remediation_proposals`, re-check that the simulation is not stale, and
  refuse while `inputs_needed` is non-empty (approval is allowed with
  placeholders unresolved; the audit entry records them).
- Approval expiry, multi-person (four-eyes) approval, and notifications.
