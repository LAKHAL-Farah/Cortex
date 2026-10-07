"""Remediation Agent (v1.3, roadmap item 4.1) -- turns a recognized problem
into one concrete, plain-language *proposed fix*: the exact command to run,
why, how risky it is, what you still have to fill in, how to undo it, and how
to check it worked.

**Proposal only. This agent never executes anything.** Simulation, the
validation UI, Ansible/OpenStack execution and the audit trail are the later
Remediation Copilot items; this is the first rung of that ladder -- the
agent *says* what it would do, a human decides. `fix_proposal` therefore
always carries `status="proposed"`, `executed=False`, `requires_approval=True`
so the later items have a stable object to attach simulation/approval/audit to
without this node's output changing shape.

**Deliberately not an LLM call**, for the same reason ADR-0008 made the
OpenStack Expert's symptom matcher deterministic and nodes/critic.py's checks
exact: a command a person is about to run on production infrastructure must
come from the reviewed catalog (openstack_expert_catalog.py), never from a
model's improvisation, and it must be reproducible -- the same incident
yields the same proposal. What this node adds on top of the expert's generic
"what's usually done about it" list is *judgement over that list*, applied
deterministically:

- picks ONE recommended step instead of dumping every option: a step that is
  fully runnable with what Cortex already knows (the host is filled in, no
  `<instance_id>` left to guess) beats one that needs more input, and within
  that the lowest-risk one wins -- which is how "stop new VMs landing on the
  struggling host" gets proposed ahead of "reboot a guest we haven't
  identified yet";
- classifies each step's risk from the command itself (`_classify_risk`);
- lists the placeholders the person must still supply (`_placeholders`);
- derives an exact undo where one exists (`_derive_undo`);
- reuses the expert's read-only confirm commands as the "check before and
  after" block.

Where it sits in the graph (see graph.py):

- **Chained**, after openstack_expert: any diagnosis the expert matched to a
  catalog entry with a state-changing step gets a proposal appended to the
  expert's walkthrough (`should_propose_fix`). A standalone expert question
  gets one only when the question asks for a fix ("how do I fix ...") -- a
  plain "how do I check X" stays a pure check answer.
- **Direct**, from the router ("propose a fix for the high CPU on
  compute-02"): this node runs the expert's catalog matching itself first,
  then proposes. When nothing in the catalog matches it says so honestly
  instead of inventing a command.

Everything in the rendered text is also in `raw_data` (the critic's numeric
grounding check reads raw_data) -- keep boilerplate in this file free of
digits so it cannot trip that check, and add no number to the summary that
isn't derived from the matched catalog entry / evidence.
"""
import hashlib
import logging
import re
from typing import Literal, NotRequired, Optional, TypedDict

from ..state import CortexState
from .openstack_expert import openstack_expert_agent

logger = logging.getLogger(__name__)

RiskLevel = Literal["low", "medium", "high"]
_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

_RISK_PHRASE: dict[str, str] = {
    "low": "low risk -- easy to reverse and unlikely to affect running workloads",
    "medium": (
        "medium risk -- it changes live service behaviour, so do it in a quiet moment "
        "and check what is running on the host first"
    ),
    "high": (
        "high risk -- it can interrupt or lose running workloads, so get a second person "
        "to review the exact command before anyone runs it"
    ),
}


class FixStep(TypedDict):
    command: str
    description: str
    risk: RiskLevel
    read_only: bool
    # `<instance_id>`-style tokens still left in `command` after the host was
    # filled in -- values only the person (or a later step) can supply.
    placeholders: list[str]
    # Exact inverse command when one can be derived mechanically, else None.
    undo: Optional[str]
    undo_note: NotRequired[str]
    # Inline commentary the catalog attaches to some commands ("  (or: ...)",
    # "  (CAUTION: ...)", "  # run inside ..."), split off so `command` is
    # copy-pasteable as written. None when the command had none.
    note: Optional[str]
    # Where the command has to be run: "openstack-cli" (an API call, from any
    # machine with the CLI configured) or "on-host" (docker/systemctl/virsh --
    # only works on the node itself). The difference matters at 3am.
    run_where: str


class FixProposal(TypedDict):
    proposal_id: str  # deterministic: same incident -> same id (see _proposal_id)
    status: str  # always "proposed" here -- later roadmap items advance it
    executed: bool  # always False -- this agent never runs anything
    requires_approval: bool  # always True
    symptom_id: str
    symptom_title: str
    host: Optional[str]
    evidence: str
    plain_language: str
    primary: FixStep
    alternatives: list[FixStep]
    verify_commands: list[dict]
    inputs_needed: list[str]
    doc_ref: str


# --------------------------------------------------------------------
# Command analysis (all pure, all unit-tested)
# --------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_][\w\-]*>")

# Checked most-severe first; the first tier with a hit wins. Substring/regex
# over the *whole* command text, so a compound catalog command ("A (then, if
# ...) B") is rated by its most dangerous part, never its mildest.
_HIGH_RISK = [
    r"\bevacuate\b", r"\bdelete\b", r"\bpurge\b", r"\bwipe\b", r"\bmkfs\b",
    r"\bshutdown\b", r"\bpoweroff\b", r"\bhalt\b", r"\breset-state\b",
    r"\bkill\s+-(?:9|KILL)\b", r"\brm\s+-", r"--force\b",
    # `docker system prune -a --volumes` and friends: irreversible bulk removal.
    r"\bprune\b", r"\bremoves?\s+all\b",
    # Rebuilding a guest reimages its root disk -- data loss, not a restart.
    r"\brebuild\b",
]
_MEDIUM_RISK = [
    r"\brestart\b", r"\bstop\b", r"\bkill\b", r"\bpkill\b", r"\breset\b",
    r"--disable\b", r"\bmigrate\b", r"\bresize\b", r"\bunset\b",
    r"\bserver\s+reboot\b",
    # The catalog's own warning marker -- if its authors flagged a command as
    # needing care, it is never rated "low" regardless of the verbs in it.
    r"\bcaution\b",
]
# A bare host `reboot` takes every guest down with it; `openstack server
# reboot` (one guest) is handled as medium above, so it is stripped before
# the bare-reboot check rather than mistaken for it.
_SERVER_REBOOT_RE = re.compile(r"\bserver\s+reboot\b")
_BARE_REBOOT_RE = re.compile(r"(?:^|[\s;&|(])(?:sudo\s+)?reboot\b")


def _classify_risk(command: str, read_only: bool) -> RiskLevel:
    """Risk of running `command`, judged from its text. A read-only step is
    "low" by definition; a state-changing step defaults to "low" only if
    nothing in it matches a medium/high pattern."""
    if read_only:
        return "low"
    text = command.lower()
    without_server_reboot = _SERVER_REBOOT_RE.sub("", text)
    if any(re.search(p, text) for p in _HIGH_RISK) or _BARE_REBOOT_RE.search(without_server_reboot):
        return "high"
    if any(re.search(p, text) for p in _MEDIUM_RISK):
        return "medium"
    return "low"


# Placeholders the person asking can fill in without investigating anything
# (they know which host they mean). Everything else -- <instance_id>, <pid>,
# <port_id> -- has to be *discovered* first, which is what makes a step not
# "runnable now".
_TRIVIAL_PLACEHOLDERS = {"<host>", "<hostname>", "<hypervisor_hostname>"}


def _needs_discovery(step: "FixStep") -> bool:
    return any(p not in _TRIVIAL_PLACEHOLDERS for p in step["placeholders"])


def _placeholders(command: str) -> list[str]:
    """Distinct unresolved `<token>`s in order of first appearance."""
    seen: list[str] = []
    for token in _PLACEHOLDER_RE.findall(command):
        if token not in seen:
            seen.append(token)
    return seen


_DISABLE_REASON_RE = re.compile(r"\s+--disable-reason\s+(?:\"[^\"]*\"|'[^']*'|\S+)")


def _derive_undo(command: str) -> Optional[str]:
    """The exact inverse of `command` when it is mechanically derivable --
    deliberately a short list of unambiguous cases rather than a guess:
    `compute service set --disable ...` <-> `--enable`, and
    `systemctl|docker stop X` <-> `start X`. Anything else returns None and
    the rendered proposal says so plainly instead of inventing an undo."""
    if "compute service set" in command and re.search(r"--disable\b", command):
        undone = _DISABLE_REASON_RE.sub("", command)
        return re.sub(r"--disable\b", "--enable", undone).strip()
    if re.search(r"\b(?:systemctl|docker)\s+stop\b", command):
        return re.sub(r"\bstop\b", "start", command, count=1).strip()
    return None


def _undo_note(command: str, undo: Optional[str]) -> str:
    if undo:
        return "Reverse it with the command below."
    if re.search(r"\brestart\b", command.lower()):
        return "Nothing to undo -- a restart can simply be repeated."
    return "There is no one-line undo for this step; use the checks below to confirm the state before and after."


_NOTE_PAREN_RE = re.compile(r"^(?P<cmd>.*?\S)\s{2,}\((?P<note>.*)\)\s*$", re.DOTALL)
_NOTE_HASH_RE = re.compile(r"^(?P<cmd>.*?\S)\s+#\s+(?P<note>.*)$", re.DOTALL)


def _split_note(text: str) -> tuple[str, Optional[str]]:
    """`"kill -TERM <pid>  (host-side only ...)"` -> (`"kill -TERM <pid>"`,
    `"host-side only ..."`). A compound command with commentary in the
    *middle* ("A  (then, if ...) B") has no clean split and is returned whole
    rather than mangled."""
    match = _NOTE_PAREN_RE.match(text) or _NOTE_HASH_RE.match(text)
    if not match:
        return text, None
    return match["cmd"], match["note"].strip()


_API_CLI_PREFIXES = ("openstack ", "nova ", "cinder ", "neutron ", "glance ", "keystone ")


def _run_where(command: str) -> str:
    return "openstack-cli" if command.lstrip().startswith(_API_CLI_PREFIXES) else "on-host"


def _to_step(command: dict) -> FixStep:
    original = command["command"]
    text, note = _split_note(original)
    read_only = bool(command.get("read_only"))
    undo = None if read_only else _derive_undo(text)
    step: FixStep = {
        "command": text,
        "description": command["description"],
        # Rated on the ORIGINAL text so a "(CAUTION: ...)" note still counts.
        "risk": _classify_risk(original, read_only),
        "read_only": read_only,
        "placeholders": _placeholders(text),
        "undo": undo,
        "note": note,
        "run_where": _run_where(text),
    }
    if not read_only:
        step["undo_note"] = _undo_note(text, undo)
    return step


def _heads_up(note: str) -> str:
    """A catalog note as a standalone sentence -- drops its own leading
    "CAUTION:" (the "Heads-up:" label already says it) and capitalizes."""
    text = re.sub(r"^\s*CAUTION:\s*", "", note, flags=re.IGNORECASE).strip()
    return _as_sentence(text[:1].upper() + text[1:])


def _as_sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def _proposal_id(symptom_id: str, host: Optional[str], primary_command: str) -> str:
    digest = hashlib.sha1(f"{symptom_id}|{host or ''}|{primary_command}".encode()).hexdigest()
    return f"fix-{digest[:12]}"


# --------------------------------------------------------------------
# Building the proposal from the expert's raw_data
# --------------------------------------------------------------------

# How many of the expert's read-only confirm commands to repeat as the
# "check before and after" block -- the first few are the most diagnostic
# (see the catalog's own ordering); more than this is a wall of text.
_MAX_VERIFY_COMMANDS = 2


def build_fix_proposal(raw_data: dict) -> Optional[FixProposal]:
    """The proposal for an openstack_expert catalog match, or None when there
    is nothing honest to propose (not a catalog match, or the entry has no
    state-changing step). Reads only the expert's `raw_data` -- never the
    database, the network, or an LLM."""
    if raw_data.get("source") != "catalog" or not raw_data.get("matched_symptom_id"):
        return None

    steps = [_to_step(c) for c in raw_data.get("remediation_commands") or []]
    actionable = [(i, s) for i, s in enumerate(steps) if not s["read_only"]]
    if not actionable:
        return None

    # Runnable-now beats needs-something-discovered (an unidentified instance
    # or pid; a missing host name doesn't count -- the person knows which host
    # they mean); then lowest risk; then fewest blanks; then the catalog's own
    # order (it lists the most common remedy first).
    actionable.sort(key=lambda pair: (
        _needs_discovery(pair[1]), _RISK_RANK[pair[1]["risk"]], bool(pair[1]["placeholders"]), pair[0],
    ))
    primary = actionable[0][1]
    alternatives = [s for _, s in actionable[1:]]

    verify = []
    for c in raw_data.get("confirm_commands") or []:
        if not c.get("read_only", True):
            continue
        cmd, note = _split_note(c["command"])
        verify.append({"command": cmd, "description": c["description"], "note": note})
    verify = verify[:_MAX_VERIFY_COMMANDS]

    host = raw_data.get("hostname")
    title = raw_data.get("matched_symptom_title") or raw_data["matched_symptom_id"]
    evidence = (raw_data.get("evidence_line") or "").strip()

    plain_parts = [evidence] if evidence else []
    plain_parts.append(f"This matches a known issue: {title}.")
    if primary["placeholders"]:
        plain_parts.append(
            "No step on file can be spelled out in full from what Cortex knows yet, so the one below is "
            "the lowest-risk option and needs a value from you first."
        )
    else:
        plain_parts.append("The safest step Cortex can already spell out in full is the one below.")
    plain_parts.append(f"It is {_RISK_PHRASE[primary['risk']]}.")

    return {
        "proposal_id": _proposal_id(raw_data["matched_symptom_id"], host, primary["command"]),
        "status": "proposed",
        "executed": False,
        "requires_approval": True,
        "symptom_id": raw_data["matched_symptom_id"],
        "symptom_title": title,
        "host": host,
        "evidence": evidence,
        "plain_language": " ".join(plain_parts),
        "primary": primary,
        "alternatives": alternatives,
        "verify_commands": verify,
        "inputs_needed": list(primary["placeholders"]),
        "doc_ref": raw_data.get("doc_ref", ""),
    }


# --------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------

def _callout(kind: str, body: str) -> str:
    # Same GitHub-style admonition the web client's Markdown renderer turns
    # into a callout (see openstack_expert._callout / compose._warning).
    return f"> [!{kind}]\n" + "\n".join(f"> {ln}" for ln in body.strip().splitlines())


def _code(command: str, indent: str = "") -> str:
    return f"{indent}```bash\n{indent}{command}\n{indent}```"


def _render_primary(step: FixStep, host: Optional[str]) -> str:
    label = f"state-changing, {step['risk']} risk"
    lines = [f"- **{step['description']}** _({label})_", _code(step["command"], "  ")]
    if step.get("note"):
        lines.append(f"  Heads-up: {_heads_up(step['note'])}")
    if step["run_where"] == "on-host":
        where = f"the host itself (`{host}`), for example over SSH" if host else "the affected host itself, for example over SSH"
        lines.append(f"  Run it on {where}.")
    else:
        lines.append("  Run it from any machine where the `openstack` CLI is set up for this cloud.")
    if step["placeholders"]:
        needed = ", ".join(f"`{p}`" for p in step["placeholders"])
        lines.append(f"  Fill in before running: {needed}.")
    lines.append(f"  {step.get('undo_note', '')}".rstrip())
    if step["undo"]:
        lines.append(_code(step["undo"], "  "))
    return "\n".join(lines)


def render_fix_proposal(proposal: FixProposal) -> str:
    approval = _callout(
        "NOTE",
        "**Proposal only -- nothing has been run.** Review the exact command, fill in anything "
        "marked as needed, and run it yourself. Cortex will not change your infrastructure on its own.",
    )
    verify = "\n".join(
        f"- **{v['description']}**\n{_code(v['command'], '  ')}"
        + (f"\n  Note: {_as_sentence(v['note'])}" if v.get("note") else "")
        for v in proposal["verify_commands"]
    )
    parts = [
        f"### Proposed fix: {proposal['symptom_title']}",
        approval,
        f"#### In plain terms\n{proposal['plain_language']}",
        f"#### The fix\n{_render_primary(proposal['primary'], proposal['host'])}",
    ]
    if verify:
        parts.append(f"#### Check it yourself, before and after\n{verify}")
    if proposal["alternatives"]:
        alts = "\n".join(
            f"- **{a['description']}** _({a['risk']} risk)_\n{_code(a['command'], '  ')}"
            + (f"\n  Heads-up: {_heads_up(a['note'])}" if a.get("note") else "")
            for a in proposal["alternatives"]
        )
        parts.append(f"#### If that is not the right move\n{alts}")
    if proposal["doc_ref"]:
        parts.append(f"---\n**Deeper reference:** {proposal['doc_ref']}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------
# Graph integration
# --------------------------------------------------------------------

# A question that asks for a fix, not just a check. Deliberately matches on
# intent words only -- "how do I check nova-compute" must stay a pure check
# answer, so nothing here fires on check/confirm/show/status phrasing.
_FIX_INTENT_RE = re.compile(
    r"\b(?:fix|fixing|resolve|resolving|remediat\w*|repair|mitigat\w*|recover\w*|solve|"
    r"workaround|propose|what\s+should\s+i\s+(?:do|run)|how\s+(?:do|can|should)\s+(?:i|we)\s+"
    r"(?:fix|resolve|solve|recover))\b",
    re.IGNORECASE,
)


def asks_for_fix(query: str) -> bool:
    return bool(_FIX_INTENT_RE.search(query or ""))


def should_propose_fix(state: CortexState) -> bool:
    """Conditional edge off openstack_expert (graph.py): does what the expert
    just produced deserve a fix proposal? Mirrors what `remediation_agent`
    checks internally, so a "yes" here is never followed by "actually,
    nothing to propose" inside the node."""
    if state.get("error") or not state.get("agent_result"):
        return False
    raw_data = state["agent_result"].get("raw_data") or {}
    if "fix_proposal" in raw_data:
        return False  # already proposed -- idempotent if the edge is ever re-entered
    if build_fix_proposal(raw_data) is None:
        return False
    # A diagnosis the expert was chained into (an incident) always gets a
    # proposal; a standalone question only when it asked for one.
    return bool(raw_data.get("diagnosed_by")) or asks_for_fix(state.get("user_query", ""))


_NO_CATALOG_NOTE = _callout(
    "NOTE",
    "**No vetted fix on file for this.** I only propose commands from the reviewed runbook "
    "catalog, and nothing in it matches that yet, so I'm not going to invent one. Name the exact "
    "symptom or service (for example \u201chigh CPU\u201d or \u201cnova-compute down\u201d) for a concrete proposal, "
    "or ask me to investigate the node first. Closest reference material, if any, follows.",
)
_NO_STEP_NOTE = _callout(
    "NOTE",
    "**No state-changing fix on file for this symptom.** The runbook entry only has read-only checks, "
    "so there is nothing for me to propose beyond them.",
)


def remediation_agent(state: CortexState) -> CortexState:
    # Reached straight from the router: nothing has matched a symptom yet, so
    # run the expert's catalog matching first (same call the graph's own
    # openstack_expert node makes for a standalone question).
    direct = (
        state.get("target_agent") == "remediation"
        and not state.get("agent_result")
        and not state.get("error")  # the expert's standalone path would silently clear it
    )
    if direct:
        state = openstack_expert_agent(state)

    result = state.get("agent_result")
    if state.get("error") or not result:
        return state

    raw_data = result.get("raw_data") or {}
    try:
        proposal = build_fix_proposal(raw_data)
        rendered = render_fix_proposal(proposal) if proposal else None
    except Exception:  # noqa: BLE001 -- a proposal is an add-on; never cost the turn its diagnosis
        logger.exception("remediation: building a fix proposal failed, keeping the upstream answer as-is")
        proposal, rendered = None, None

    if proposal is None or rendered is None:
        if direct:
            note = _NO_STEP_NOTE if raw_data.get("source") == "catalog" and raw_data.get("matched_symptom_id") else _NO_CATALOG_NOTE
            state["agent_result"] = {**result, "summary": f"{note}\n\n{result['summary']}"}
            state["target_agent"] = "remediation"
        return state

    state["agent_result"] = {
        **result,
        "summary": f"{result['summary']}\n\n---\n\n{rendered}",
        "raw_data": {**raw_data, "fix_proposal": proposal},
    }
    state["target_agent"] = "remediation"
    return state
