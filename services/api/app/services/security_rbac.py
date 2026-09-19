"""Shared RBAC output-filtering for the Security Agent's findings (v0.9,
Phase Sec-2).

Originally lived only in routers/agents.py, applied once to the chat
path (POST /orchestrate, GET /trace/{trace_id}). Phase Sec-2 adds a
second caller -- routers/security.py's dashboard panel endpoints -- and
the whole point of that phase is "a viewer-role gets the same protection
whether they ask via chat or load the dashboard" (see that router's
module docstring), which is only true if both routers call the exact
same functions rather than each keeping their own copy that can quietly
drift out of sync. This module is that one shared copy; routers/agents.py
and routers/security.py both import from here, neither defines its own.

See nodes/security.py's module docstring for *why* this exists at all:
a viewer account seeing exactly which world-open port, which CVE, which
confirmed-listening exposed port, or which eBPF alert line to exploit is
a real exposure, not just an information nicety -- Monitoring/Prediction/
Network's raw findings don't carry that risk, Security's does.
"""
_SECURITY_SUB_SIGNAL_KEYS = (
    "auth_signal", "sec_group_signal", "cve_signal", "exposed_port_signal", "ebpf_signal",
    # Not part of nodes/security.py's five node-scoped sub-checks -- these
    # two are built directly by routers/security.py's own Sec-5b/Sec-5c
    # endpoints (instance-scope exposed-port probe, fleet-wide Keystone
    # token-abuse) -- but they carry exactly the same kind of
    # individually-exploitable specifics (a real reachable IP:port, a
    # real username/source-IP pattern), so they get the same redaction
    # treatment through this one shared list rather than a second,
    # parallel redaction path.
    "instance_exposure_signal", "keystone_token_signal",
)
RESTRICTED_NOTICE = (
    "This turn's finding involved the Security Agent (auth activity, security-group rules, "
    "known-vulnerable packages, or kernel-level alerts). Those specifics are restricted to admin "
    "accounts -- ask an admin to review this trace, or sign in with an admin account to see the "
    "full finding."
)


def security_agent_involved(agent_used: str, raw_data: dict | None) -> bool:
    """True if the Security Agent contributed *any* finding this turn --
    as the direct answer (`agent_used == "security"`), as arbitration's
    chosen primary theory for a cross-agent incident
    (`raw_data["investigating_agent"]`), or merely as one of several
    corroborating/competing findings arbitration reported alongside
    another agent's primary theory (`cross_agent_findings`/
    `multi_node_findings`, see agents/nodes/anomaly.py's
    `anomaly_arbitrate`). That last case matters just as much as the
    first two: the cross-agent narrative an LLM writes from *all*
    contributing findings can surface a CVE ID or an eBPF alert's output
    line in prose even when Security wasn't the winning theory -- so
    "involved at all", not just "was primary", is the right question for
    whether this response needs filtering.
    """
    if agent_used == "security":
        return True
    raw_data = raw_data or {}
    if raw_data.get("investigating_agent") == "security":
        return True
    for key in ("cross_agent_findings", "multi_node_findings"):
        if any(f.get("agent") == "security" for f in raw_data.get(key) or []):
            return True
    return False


def redact_security_raw_data(raw_data: dict | None) -> dict | None:
    """Strips this turn's raw_data down to what's safe for a non-admin:
    booleans and hostnames survive (a viewer can still see "something was
    flagged on compute-02"), but the specific evidence -- which CVE,
    which security-group rule or drift entry, which eBPF alert line,
    which auth-log entry -- does not, since (per nodes/security.py's
    module docstring) that's each individually exploitable, not just
    informative. Only Security's own sub-signals and Security's own
    entries in a cross-agent breakdown are touched; another agent's
    (Network's, Anomaly's) own findings in the same raw_data are left
    exactly as they were, since those aren't the sensitive part of this
    turn.
    """
    if raw_data is None:
        return None
    redacted = dict(raw_data)

    for key in _SECURITY_SUB_SIGNAL_KEYS:
        signal = redacted.get(key)
        if isinstance(signal, dict):
            redacted[key] = {"has_signal": signal.get("has_signal"), "degraded": signal.get("degraded"), "restricted": True}

    # v1.2: the arbitration ledger (agents/incident_fanout.py) carries the
    # security agent's confidence/score -- reduce that row to "ran, flagged or
    # not" like every other Security field here. `winner_summary` is already
    # None whenever Security won.
    arbitration = redacted.get("arbitration")
    if isinstance(arbitration, dict):
        redacted["arbitration"] = {
            **arbitration,
            "theories": [
                {"agent": t.get("agent"), "has_signal": t.get("has_signal"), "verdict": t.get("verdict"), "restricted": True}
                if t.get("agent") == "security" else t
                for t in arbitration.get("theories") or []
            ],
        }

    for key in ("cross_agent_findings", "multi_node_findings"):
        entries = redacted.get(key)
        if not entries:
            continue
        redacted[key] = [
            {"hostname": f.get("hostname"), "agent": f.get("agent"), "has_signal": f.get("has_signal"), "restricted": True}
            if f.get("agent") == "security" else f
            for f in entries
        ]

    return redacted


def filter_security_response_for_role(answer: str, raw_data: dict | None, agent_used: str, role: str) -> tuple[str, dict | None]:
    """Applied once per response, right before it's built -- by
    routers/agents.py's POST /orchestrate and GET /trace/{trace_id}, and
    by routers/security.py's dashboard panel endpoints (Phase Sec-2),
    all calling this same function rather than each keeping their own
    copy.

    Admins always get the full answer/raw_data -- see auth.py's
    User.role, "admin" | "viewer". A non-admin gets a generic redaction
    notice in place of the prose answer (the free-form arbitration/
    narration text can name specifics for any agent that contributed, not
    just the primary one, so partial prose-scrubbing isn't safe -- see
    `security_agent_involved`'s own docstring) and a raw_data with only
    Security's own fields stripped down to booleans
    (`redact_security_raw_data`).
    """
    if role == "admin" or not security_agent_involved(agent_used, raw_data):
        return answer, raw_data
    return RESTRICTED_NOTICE, redact_security_raw_data(raw_data)


def filter_security_steps_for_role(steps: list[dict], involved: bool, role: str) -> list[dict]:
    """Same rule as `filter_security_response_for_role`, applied per-step
    to a trace timeline. Only two step *names* can ever carry Security
    specifics: "security" itself (the standalone leaf agent, see
    nodes/security.py) and "anomaly" (the cross-agent arbitration join
    node, see nodes/anomaly.py's `anomaly_arbitrate` -- its own narrative
    can name a CVE/eBPF alert from a *corroborating* Security finding even
    when Security wasn't the winning theory). Every other step name
    (router/monitoring/network/openstack_expert/critic/compose) is
    structurally incapable of repeating Security's own findings, so
    `involved` -- the caller's already-computed top-level answer/raw_data
    involvement flag, see `security_agent_involved` -- is reused directly
    rather than re-deriving it per step.
    """
    if role == "admin" or not involved:
        return steps

    filtered = []
    for step in steps:
        if step.get("node") not in ("security", "anomaly"):
            filtered.append(step)
            continue
        new_detail = dict(step.get("detail") or {})
        new_detail["summary"] = RESTRICTED_NOTICE
        new_detail.pop("chained_from", None)
        filtered.append({**step, "detail": new_detail})
    return filtered
