# ADR-0015: Approved fixes execute only after an explicit click (roadmap 4.4)

**Status:** Accepted
**Related code:** `services/api/app/services/remediation_execution.py` (the gate),
`services/api/app/services/remediation_executor.py` (plan + backends),
`services/api/app/services/proposal_qa.py`, `services/api/app/routers/remediation.py`
(`POST .../execute`), `services/api/alembic/versions/a4c6e8f0b2d4_add_remediation_execution.py`,
`infra/ansible/playbook-remediate.yml` (and the same file in `infra/ansible-sandbox/`),
`services/web/components/RemediationApproval.tsx`
**Related ADRs:** adr-0011 (the proposal), adr-0013 (its simulation), adr-0014 (the approval and the audit chain this extends)

## Context

Roadmap 4.4: *approved fixes execute via Ansible/OpenStack SDK only after explicit
click.* Acceptance: *nothing executes without approval (Confidence Ladder
Level 0).* 4.3 left `approved` meaning only "a person signed off"; this adds the
step that actually changes infrastructure, and the rules that keep it a human act.

Level 0 is read here as: **Cortex recommends; a person approves; a person
clicks; only then does anything run.** There is no exception for low-risk fixes,
for high agent confidence, or for the proposal looking familiar. Auto-running
anything is a later level and would be a reviewed change to
`remediation_execution.CONFIDENCE_LEVEL` and `start_execution`, not a setting.

## Decisions

1. **Approving and executing are two acts.** `approve` still records a decision
   and nothing else. Execution is `POST /api/v1/remediation/proposals/{id}/execute`,
   and the UI puts a confirmation between the button and the call that names
   what will happen.
2. **One gate, in the service, not the router or the UI.**
   `remediation_execution.start_execution` is the only path to a run, and it
   checks, in order: admin role (403); execution not switched off (503);
   the click names the digest of the proposal the person saw (409); the stored
   status is `approved` or `execution_failed` (409); no unresolved
   `<placeholder>` (422, as ADR-0014 required); the command is on the allow-list
   (422); the impact is not worse than what was approved (409); then, under the
   row lock, **the audit trail itself contains an `approved` entry** (409) and
   that approval is younger than the TTL (409). `status` is a cache of the
   trail, so a status flipped by a bug or a manual UPDATE is not an approval.
3. **What runs is the stored snapshot, parsed, never a shell string.**
   `plan_for` turns the proposal's command into a typed `ExecutionPlan` using
   strict patterns (one command; no `;` `&&` `|` `$(` backticks or notes;
   identifiers matched against UUID/name regexes; no `--x=y`). The backends get
   fields, not text. Anything that does not parse is `NotExecutable` with a
   reason shown on the card; the fix stays approvable and the person runs it by
   hand, as before.
4. **The allow-list is short and reviewed:**
   - OpenStack SDK: enable/disable `nova-compute` on a host, soft/hard reboot one
     guest, enable one network agent, enable one port.
   - Ansible (`playbook-remediate.yml`): `docker restart|stop|start` on up to four
     named containers, `systemctl restart|stop|start` on one unit.
   - Never: deletes, rebuilds, prunes, `reset-state`, migrations, `kill <pid>`,
     compound commands, volume/image operations. Units Cortex depends on to reach
     the host (`sshd`, `docker`, networking) are refused too.
   Of the ~45 commands the catalog can produce for a host, the runnable ones are
   the restarts, enable/disable service, and (once their id is filled in) the
   guest/agent/port ones; the destructive alternatives are never run.
5. **Separate credentials.** The SDK connects with `OS_REMEDIATION_CLOUD`
   (default `cortex-operator`), not the read-only `cortex-reader` the collectors
   use, so the discovery path cannot change anything even if this module were
   bypassed. The operator profile must exist in `clouds.yaml`; with no profile the
   SDK path fails and records that.
6. **Ansible is constrained twice.** Values go in a private (0600) extra-vars
   JSON file, never on the command line, with `--limit <one host>` (the name
   cannot be `all`, `localhost`, `ungrouped`, or contain pattern characters). The
   playbook then refuses on its own unless the play resolved to exactly one host
   equal to `remediation_host` (so `--limit all` or a group name fails before any
   task), re-validates kind/action/targets, and runs docker via `argv`, not a
   shell. Verified against real Ansible. Success requires the host in the PLAY
   RECAP with `failed=0 unreachable=0`; exit code 0 with no host in the recap
   (`--limit` matched nothing) is a failure.
7. **Still true at click time.** The approval expires after
   `CORTEX_REMEDIATION_APPROVAL_TTL_MINUTES` (default 60), and a retry after a
   failure uses the same clock. If the proposal was simulated, it is simulated
   again; a worse verdict, an unreadable Living Model, or a simulator error all
   refuse. "Could not re-check" is not "unchanged". A proposal that was never
   simulated is not blocked for lack of one (the approver saw that).
8. **Once, and recoverable.** The status moves to `executing` under a row lock
   before anything runs; a double click or second tab gets 409. `execution_started`
   is committed *before* the action, `executed`/`execution_failed` after, all in
   the hash chain (`details.execution` holds backend, operation, targets, result,
   bounded output, duration). If the worker dies mid-run, the row stays
   `executing`; past `EXECUTION_STALE_AFTER` the next click records "outcome
   unknown" as a failure and **still does not re-run**, because an interrupted
   reboot may well have happened. A deliberate further click is then allowed.
   `execution_failed` can be retried by another click; `executed` cannot.
9. **Asynchronous.** The endpoint answers 202 and the run happens in a background
   task with its own DB session (Ansible can take minutes); the card polls while
   `executing`. A backend failure is a *result* (`ok=false`), never an exception
   that loses the outcome.
10. **Kill switch.** `CORTEX_REMEDIATION_EXECUTION=off|live`. Unset means `live`
    except `CORTEX_ENV=production`, where it is `off` until enabled on purpose
    (`docker-compose.prod.yml` sets it off explicitly). Off makes every execute a
    503 and the card says why; approvals still work.
11. **Closed after the decision.** Once past `approved`, no further decision
    (approve/reject/ask) is accepted (409); `can_decide` is only true for
    `proposed`/`info_requested`.

### "Ask for more info" now answers

4.3 logged the question and nothing answered it. Now an `info_provided` audit
entry follows each `info_requested` (a background task, so the question is
committed first and the answer cannot block or lose it).

- **Deterministic and grounded only in the stored proposal.** The answer lands
  in a tamper-evident trail as "what the person was told before deciding", so it
  must never state something the proposal does not hold. `proposal_qa` matches
  the question to up to three topics (what it runs, why, risk, impact, undo,
  how to check, alternatives, what is missing, whether Cortex can run it; English
  and French keywords) and answers each from its own snapshot fields. No LLM, no
  graph, no database read.
- A question it cannot place gets the overview and says so plainly; it never
  pretends to have answered.
- The answer is idempotent per question (`details.answers_entry_id`), does not
  change the status (the proposal stays open for a decision), is attributed to
  Cortex (no actor), and shows inline on the card.

## Consequences / limits

- **Not verified here against a real cloud.** The SDK calls match the installed
  openstacksdk signatures and are unit-tested with a fake connection; the
  `cortex-operator` profile and a reachable inventory host are deployment
  prerequisites. First real use should be on the sandbox stack.
- Execution reports what the tool returned, not that the problem is gone. The
  card tells the person to use the check commands; automatic post-verification
  is a follow-up.
- Approval-to-click is one admin acting twice; four-eyes (a different person
  must click than approved) is still out of scope, as is notification.
- Mapping a catalog command to the allow-list is by pattern. A new catalog verb
  will simply be "not executable" until someone adds a reviewed rule, which is
  the intended failure direction.
- The free-text answer covers common questions by keyword. Questions needing
  knowledge outside the proposal (asking what a colleague did yesterday) are not
  answered; a human reply path is the follow-up.

## In the sandbox

`infra/SANDBOX-REMEDIATION.md` is the walkthrough. The sandbox could not
originally *succeed* at an execution (the simulated OpenStack was read-only and
its nodes have no Docker), so it was extended rather than special-cased in the
executor: `openstack-sim` accepts the executor's SDK writes for the
`cortex-operator` identity only (the reader gets a 403, so the sandbox exercises
decision 5), logging each at `GET /_sandbox/changes`; and the sim nodes get a
`docker` shim that flips a simulated container from down to healthy. Building
this also found that the sim ignores `compute.services(host=, binary=)` filters,
which is why the executor now matches the host and binary itself and refuses an
ambiguous match (decision 4's allow-list is only as safe as the target it acts on).

## Operating it

- Add a `cortex-operator` profile to `clouds.yaml` with only the roles it needs.
- Production: set `CORTEX_REMEDIATION_EXECUTION=live` deliberately.
- Roll back: `alembic downgrade e7a1c3d5f9b2` refuses while any row uses an
  execution status/event (history is not relabelled); set the switch to `off`
  instead.
