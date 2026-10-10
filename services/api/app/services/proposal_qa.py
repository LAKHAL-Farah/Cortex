"""Answers an "ask for more information" on a proposed fix (roadmap 4.3 left
the question logged and unanswered; 4.4 closes that, adr-0015).

**Deterministic, and grounded only in the stored proposal.** The answer is
written into the append-only audit trail as "what the person was told before
they decided", so it must never contain a claim the proposal itself does not
carry. That rules out free-form generation here for the same reason the fix
itself is not an LLM call (adr-0011): the same question about the same
proposal gives the same answer, and every sentence traces back to a field of
the snapshot -- the evidence line, the catalog entry's risk and undo, the
Living-Model simulation, the verify commands, the alternatives.

How a question is answered:

1. Its words are matched against a small keyword table per *topic* (what it
   runs, why it was proposed, risk, impact, undo, how to check, alternatives,
   what is still missing, whether Cortex can run it itself).
2. The best-matching topics (at most three) are answered, each from its own
   fields.
3. A question that matches nothing gets the overview (what / why / risk /
   impact) and says plainly that the question was not specifically
   understood -- it does not pretend to have answered it.

Nothing here reads the database, the graph, OpenStack or an LLM.
"""
import re
from typing import Callable, Optional

from .remediation_executor import NotExecutable, plan_for

MAX_TOPICS = 3

# topic -> words/phrases (lower case; matched as whole words or word prefixes).
# English first; the platform's operators also write in French.
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "what": ("what does", "what will", "what is it", "command", "exactly", "action", "where", "commande"),
    "why": ("why", "reason", "cause", "evidence", "symptom", "diagnos", "because", "pourquoi", "raison"),
    "risk": ("risk", "risky", "safe", "dangerous", "danger", "critical", "high", "low", "medium", "risque", "sûr", "sur"),
    "impact": ("impact", "affect", "affected", "touch", "instance", "instances", "vm", "vms", "guest", "guests",
               "downtime", "outage", "disrupt", "workload", "workloads", "break", "capacity", "network", "simulat",
               "effet", "effets", "conséquence", "consequence"),
    "undo": ("undo", "revert", "rollback", "roll back", "reverse", "reversible", "back out", "annuler", "retour"),
    "verify": ("verify", "check", "confirm", "validate", "worked", "success", "test", "vérifier", "verifier", "contrôle"),
    "alternatives": ("alternative", "alternatives", "instead", "other option", "options", "safer", "gentler", "else",
                     "autre", "plutôt"),
    "inputs": ("missing", "fill", "placeholder", "need from me", "input", "inputs", "value", "values", "manquant"),
    "execution": ("execute", "execution", "automatic", "automatically", "cortex run", "can cortex", "manually", "by hand",
                  "click", "button", "ansible", "sdk", "exécuter", "automatique"),
}
# Order topics are answered in when several match equally.
_TOPIC_ORDER = ("what", "why", "risk", "impact", "undo", "verify", "alternatives", "inputs", "execution")


def _words(question: str) -> str:
    return " " + re.sub(r"[^\w\u00C0-\u017F' ]+", " ", question.lower()) + " "


def _score(topic: str, haystack: str) -> int:
    hits = 0
    for kw in _TOPIC_KEYWORDS[topic]:
        # Whole word/phrase, or a word that *starts* with the keyword ("simulat" -> "simulated").
        if re.search(rf"\s{re.escape(kw)}(?:\w*)\s", haystack):
            hits += 1
    return hits


def pick_topics(question: str) -> list[str]:
    haystack = _words(question)
    scored = [(t, _score(t, haystack)) for t in _TOPIC_ORDER]
    scored = [(t, s) for t, s in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[1], _TOPIC_ORDER.index(pair[0])))
    return [t for t, _ in scored[:MAX_TOPICS]]


# --------------------------------------------------------------------
# Per-topic answers (each reads only the snapshot)
# --------------------------------------------------------------------

def _primary(p: dict) -> dict:
    return p.get("primary") or {}


def _sim(p: dict) -> dict:
    return p.get("simulation") or _primary(p).get("simulation") or {}


def _a_what(p: dict) -> str:
    step = _primary(p)
    where = (
        f"on the host itself ({p['host']}), for example over SSH"
        if step.get("run_where") == "on-host" and p.get("host")
        else "from any machine where the openstack CLI is set up for this cloud"
        if step.get("run_where") == "openstack-cli"
        else "on the affected host"
    )
    lines = [f"It runs: {step.get('command')}", f"What it is for: {step.get('description')}.", f"Where: {where}."]
    if step.get("note"):
        lines.append(f"Catalog note: {step['note']}")
    return "\n".join(lines)


def _a_why(p: dict) -> str:
    lines = []
    if p.get("evidence"):
        lines.append(f"What Cortex saw: {p['evidence']}")
    lines.append(f"It matches the known issue \"{p.get('symptom_title')}\" in the reviewed runbook catalog.")
    if p.get("doc_ref"):
        lines.append(f"Reference: {p['doc_ref']}")
    return "\n".join(lines)


def _a_risk(p: dict) -> str:
    step = _primary(p)
    base, eff = step.get("risk"), step.get("effective_risk") or step.get("risk")
    lines = [f"Rated {eff} risk."]
    if base and eff and base != eff:
        lines.append(f"The command alone looked {base}; the Living Model simulation raised it to {eff}.")
    sim = _sim(p)
    if sim.get("status") == "simulated":
        lines.append(f"Simulation verdict: {sim.get('verdict')}. {sim.get('headline', '')}".strip())
    elif sim:
        lines.append(f"It was not simulated: {sim.get('headline', 'no impact information is available')}")
    else:
        lines.append("No simulation was recorded for this proposal.")
    return "\n".join(lines)


def _a_impact(p: dict) -> str:
    sim = _sim(p)
    if not sim:
        return "No impact simulation was recorded for this proposal, so there is no list of affected resources."
    if sim.get("status") != "simulated":
        return f"Impact was not simulated. {sim.get('headline', '')}".strip()
    lines = [str(sim.get("headline", ""))]
    for e in (sim.get("effects") or [])[:6]:
        detail = f" -- {e['detail']}" if e.get("detail") else ""
        lines.append(f"- {e.get('name')}: {e.get('effect')}{detail}")
    total, shown = sim.get("effects_total", 0), len(sim.get("effects") or [])
    if total > shown:
        lines.append(f"...and {total - shown} more not listed.")
    if sim.get("duration"):
        lines.append(f"Lasts: {sim['duration']}.")
    lines.append("Reversible." if sim.get("reversible") else "Not reversible with one command.")
    for w in sim.get("warnings") or []:
        lines.append(f"Warning: {w}")
    if sim.get("assumptions"):
        lines.append("Assumptions: " + " ".join(sim["assumptions"]))
    return "\n".join(line for line in lines if line)


def _a_undo(p: dict) -> str:
    step = _primary(p)
    if step.get("undo"):
        return f"To undo it, run: {step['undo']}"
    return step.get("undo_note") or "There is no one-line undo for this step; use the check commands to confirm the state before and after."


def _a_verify(p: dict) -> str:
    checks = p.get("verify_commands") or []
    if not checks:
        return "No read-only check commands are on file for this issue."
    return "Check before and after:\n" + "\n".join(f"- {c.get('description')}: {c.get('command')}" for c in checks)


def _a_alternatives(p: dict) -> str:
    alts = p.get("alternatives") or []
    safer = p.get("safer_alternative")
    lines = []
    if safer:
        lines.append(f"Simulation found a gentler option ({safer.get('verdict')}): {safer.get('description')} -- {safer.get('command')}")
    for a in alts:
        lines.append(f"- {a.get('description')} ({a.get('effective_risk') or a.get('risk')} risk): {a.get('command')}")
    return "\n".join(lines) if lines else "The catalog has no other state-changing option for this issue."


def _a_inputs(p: dict) -> str:
    needed = p.get("inputs_needed") or []
    if not needed:
        return "Nothing is missing: the command is complete as written."
    return ("Still to be filled in by a person: " + ", ".join(needed)
            + ". Cortex will not run a command with these unresolved.")


def _a_execution(p: dict) -> str:
    step = _primary(p)
    try:
        plan = plan_for(step.get("command", ""), p.get("host"))
    except NotExecutable as exc:
        return f"Cortex cannot run this one for you: {exc.reason} It can still be approved and run by hand."
    via = "the OpenStack SDK" if plan.backend == "openstack_sdk" else "Ansible"
    return (
        f"Once approved, an admin can click Execute and Cortex runs it through {via}: {plan.summary} "
        "Nothing runs without that approval and that separate click."
    )


_ANSWERERS: dict[str, tuple[str, Callable[[dict], str]]] = {
    "what": ("What it does", _a_what),
    "why": ("Why it was proposed", _a_why),
    "risk": ("Risk", _a_risk),
    "impact": ("Expected impact", _a_impact),
    "undo": ("Undo", _a_undo),
    "verify": ("How to check it", _a_verify),
    "alternatives": ("Alternatives", _a_alternatives),
    "inputs": ("What is still missing", _a_inputs),
    "execution": ("Can Cortex run it", _a_execution),
}


def answer_question(proposal: dict, question: Optional[str]) -> dict:
    """`{"answer": str, "topics": [...], "matched": bool}`. Always returns an
    answer; `matched=False` means the question was not specifically understood
    and the overview was given instead."""
    topics = pick_topics(question or "")
    matched = bool(topics)
    if not matched:
        topics = ["what", "why", "risk", "impact"]

    sections = []
    for t in topics:
        title, build = _ANSWERERS[t]
        sections.append(f"{title}\n{build(proposal)}")
    intro = (
        "Here is what is on file for this proposal."
        if matched
        else "I could not tell which part you are asking about, so here is the overview of what is on file. "
        "Ask again with a more specific question (risk, impact, undo, how to check, alternatives) or ask a colleague."
    )
    return {"answer": intro + "\n\n" + "\n\n".join(sections), "topics": topics, "matched": matched}
