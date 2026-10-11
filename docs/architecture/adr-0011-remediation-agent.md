# ADR-0011: Remediation Agent (roadmap 4.1) — a deterministic, proposal-only fix

**Status:** Accepted
**Related code:** `services/api/app/agents/nodes/remediation.py`,
`services/api/app/agents/graph.py`, `services/api/app/agents/intent_router.py`,
`services/web/components/CopilotAgentPanels.tsx` (`FixProposalPanel`)
**Related ADRs:** adr-0008 (the catalog this reads), adr-0009 (the critic it must satisfy)

## Context

Roadmap 4.1: *the remediation agent proposes a concrete fix in plain language* —
demo: trigger a known issue, the agent proposes the exact fix text. The OpenStack
Expert already lists "what's usually done about it", but as an unranked list of
every option. It never *decides*, and says nothing about risk, undo, or what is
still missing before a command can be run.

## Decisions

1. **Proposal only.** Nothing is executed. `fix_proposal` carries
   `status="proposed"`, `executed=false`, `requires_approval=true`, so the later
   Remediation Copilot items (simulation, approval UI, Ansible/OpenStack
   execution, audit) attach to a stable object. `proposal_id` is a hash of
   (symptom, host, command): the same incident yields the same id.
2. **No LLM.** Commands come only from the reviewed catalog (ADR-0008's argument:
   a command a person runs on production must not be a model's improvisation, and
   must be reproducible). What the agent adds is deterministic *judgement over the
   catalog*: one recommended step (runnable-now before needs-discovery, then lowest
   risk, then catalog order), risk from the command text (destructive verbs and the
   catalog's own `CAUTION` marker raise it), unresolved placeholders, a derived
   exact undo where one exists, where to run it (API CLI vs on the host), and the
   expert's read-only checks as "check before and after". Free-form LLM narration
   can be layered on later behind the same `fix_proposal` shape.
3. **Two ways in, same as the expert.** Chained after `openstack_expert`
   (`should_propose_fix`: always for an incident, only on fix-intent wording for a
   standalone question, so "how do I check X" stays a check) and directly from the
   router (new `remediation` target). Direct with no catalog match says so rather
   than inventing a command.
4. **Additive and fail-safe.** The proposal is appended to the expert's walkthrough,
   never a replacement; an exception while building it is swallowed so the turn
   keeps the diagnosis. `target_agent` becomes `remediation` when a proposal is
   attached (existing chain assertions updated accordingly).
5. **Critic-safe.** The critic flags numbers absent from `raw_data`; the rendered
   proposal is digit-free boilerplate plus text already in `raw_data`. A property
   test runs every catalog entry through the critic.

## Consequences

- Router routing for "fix X" now has a third candidate (rag / expert / remediation);
  the prompt distinguishes them and four golden-set cases were added. Run
  `scripts/eval_router_golden_set.py` against the real classifier before relying on it.
- The generic "What's usually done about it" section still appears above the
  proposal; collapsing it in the UI once the panel is established is a follow-up.
