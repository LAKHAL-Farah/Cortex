"""Security agent -- auth anomalies, security-group audit, exposed/known-
vulnerable packages, and kernel-level (eBPF) signals for one node (v0.9,
roadmap Phase 5: "Security & Network agents").

Same sub-orchestration shape nodes/anomaly.py established (v0.4) and this
module deliberately reuses rather than reinvents: several near-independent
sub-checks run inside this one graph node and get merged into a single
AgentResult before the graph ever sees them. Anomaly needed two sub-checks
(metric + log); Security needs four, since "is this node compromised or
misconfigured" genuinely has four largely-independent angles, none of
which alone is conclusive:

1. `_check_auth_anomaly` -- correlated auth-failure log lines (failed
   password, invalid user, repeated sudo failures) from Loki, the same
   client anomaly.py's `_check_logs` already uses, just pattern-matched
   against auth-specific keywords instead of generic error/warn/exception
   ones. Log-only, and therefore the weakest of the four signals on its
   own -- an attacker who's already in doesn't have to keep failing
   logins, and a burst of failed logins doesn't by itself mean anyone
   succeeded. See _CONFIDENCE_BY_SIGNAL for exactly how this is weighted
   below eBPF/sec-group/CVE.
2. `_check_sec_group_diff` -- Neutron security-group rules attached to
   this host's instances, audited against a built-in "overly permissive"
   baseline (services/security_audit.py) -- a world-open SSH/RDP/database
   port, or an all-protocols-all-ports rule. This is *configuration*
   evidence: it says the door is open, not that anyone walked through it.
3. `_check_cve_match` -- this host's installed package versions
   (services/cve_feed.py) matched against a small embedded CVE reference
   table. Also configuration evidence (a known-vulnerable version is
   installed), not proof of exploitation.
4. `_check_ebpf_signal` -- live kernel-level alerts from a Falco/Tetragon-
   style sensor (services/ebpf_signal.py) -- process execs, syscalls,
   file/network activity the kernel itself observed. This is the only one
   of the four that's actual *behavioral* evidence rather than
   configuration or log-based inference, and the hardest for an attacker
   to fake or avoid (they can avoid writing to a log Loki reads, or
   already be in before a CVE scan runs, but they can't make the kernel
   not see a process exec) -- hence the roadmap's explicit "confidence
   weighted toward eBPF/kernel signals... over log-only signals", applied
   in `_confidence` below.

Honesty about what's real vs. illustrative in an openstack-sim checkout:
`_check_auth_anomaly` and `_check_sec_group_diff` read genuinely live data
(the same Loki/Neutron this project already runs). `_check_cve_match` and
`_check_ebpf_signal` are real HTTP clients (services/cve_feed.py,
services/ebpf_signal.py) against endpoints (`CORTEX_PACKAGE_INVENTORY_URL`,
`CORTEX_EBPF_ALERTS_URL`) nothing in this repo stands up yet -- point them
at a real package-inventory collector / Falco or Tetragon deployment and
they read real data; until then they simply fail to connect, which (same
as every other external read in this codebase) degrades that one sub-check
to "unknown" via resilience.get_breaker rather than crashing the turn or
silently reporting "clean". `_CVE_DATABASE` itself (cve_feed.py) is a
small, real, but deliberately finite hand-picked table, same tradeoff
openstack_expert_catalog.py already makes for its runbook entries.

RBAC note: this agent's raw findings (which CVEs, which security-group
rules, which specific eBPF alert lines) are sensitive in a way Monitoring/
Prediction/Network's aren't -- a viewer account seeing "here's exactly
which world-open port and package version to exploit" is a real exposure,
not just an information nicety. That filtering happens in
routers/agents.py (`_filter_security_response_for_role`), not here --
this module always computes and returns the full finding; the endpoint is
what decides how much of it a given caller's role gets to see, the same
place get_current_user's role claim is already available and every other
per-role gate in this codebase already lives (see auth.py's require_admin).
"""
import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage

from ...services import cve_feed, ebpf_signal, loki_client, security_audit
from ...services.llm_client import LLMConfigError, get_chat_model
from ..node_resolver import resolve_node
from ..resilience import get_breaker, guarded_send
from ..state import AgentResult, CortexState, IncidentFinding, KnownNode

logger = logging.getLogger(__name__)

# How far back _check_auth_anomaly looks -- there's no metric-signal
# timestamp to anchor a window on the way anomaly.py's log-check has (a
# security question is rarely "around this specific spike"), so this is
# just a fixed recent lookback.
_AUTH_LOG_WINDOW_MINUTES = 60
_AUTH_LOG_SIGNAL_PATTERN = (
    "(?i)failed password|authentication failure|invalid user|permission denied|"
    "too many authentication failures|pam_unix.*auth.*fail"
)

# Confidence weighting: eBPF (kernel-level, behavioral, hardest to fake or
# miss) > sec-group-diff / CVE-match (configuration evidence, real but
# doesn't itself prove exploitation) > auth-anomaly alone (log-only,
# easiest for an attacker to avoid leaving a trace of, or to simply not
# be caused by an attacker at all -- a misconfigured cron job can also
# fail logins repeatedly). This ladder is the literal roadmap requirement
# ("confidence weighted toward eBPF/kernel signals... over log-only
# signals") -- see _confidence.
_EBPF_SIGNAL_CONFIDENCE = 0.95
_CONFIG_SIGNAL_CONFIDENCE = 0.8  # sec-group-diff and/or CVE-match
_AUTH_LOG_ONLY_CONFIDENCE = 0.55
_NO_SIGNAL_CONFIDENCE = 0.9
_DEGRADED_CONFIDENCE_CAP = 0.6


# --------------------------------------------------------------------
# Sub-check 1: auth-anomaly (Loki)
# --------------------------------------------------------------------

def _check_auth_anomaly(node: KnownNode) -> dict:
    now = time.time()
    start = now - _AUTH_LOG_WINDOW_MINUTES * 60
    logql = f'{{host="{node["hostname"]}"}} |~ "{_AUTH_LOG_SIGNAL_PATTERN}"'

    breaker = get_breaker("security.loki", timeout_seconds=8.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(loki_client.query_range, logql, start, now, limit=50)

    if not call_result.ok:
        logger.warning("security_agent: auth-anomaly sub-check failed to query Loki: %s", call_result.failure)
        return {
            "has_signal": False,
            "degraded": True,
            "failure": call_result.failure,
            "detail": (
                "The auth-log check couldn't complete (the log store didn't respond in time), "
                "so authentication activity for this host is unknown rather than confirmed clean."
            ),
            "entries": [],
        }

    entries = []
    for stream in call_result.value:
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entries.append({"ts": int(ts_ns) // 1_000_000, "line": line, "service": labels.get("service") or labels.get("job")})
    entries.sort(key=lambda e: e["ts"], reverse=True)

    if not entries:
        return {
            "has_signal": False,
            "degraded": False,
            "detail": f"No correlated auth-failure log entries found for {node['hostname']} in the last {_AUTH_LOG_WINDOW_MINUTES} minutes.",
            "entries": [],
        }

    top = entries[:5]
    plural = "y" if len(entries) == 1 else "ies"
    detail = (
        f"{len(entries)} correlated auth-failure log entr{plural} for {node['hostname']} in the last "
        f"{_AUTH_LOG_WINDOW_MINUTES} minutes, most recent: \"{top[0]['line'][:160]}\"."
    )
    return {"has_signal": True, "degraded": False, "detail": detail, "entries": top}


# --------------------------------------------------------------------
# Sub-check 2: sec-group-diff (Neutron)
# --------------------------------------------------------------------

def _check_sec_group_diff(node: KnownNode) -> dict:
    breaker = get_breaker("security.neutron", timeout_seconds=10.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(security_audit.get_node_security_groups, node["hostname"])

    if not call_result.ok:
        logger.warning("security_agent: sec-group-diff sub-check failed: %s", call_result.failure)
        return {
            "has_signal": False,
            "degraded": True,
            "failure": call_result.failure,
            "detail": (
                "The security-group check couldn't complete (OpenStack's network API didn't "
                "respond in time), so this host's firewall posture is unknown rather than confirmed safe."
            ),
            "data": None,
            "risky_rules": [],
        }

    data = call_result.value
    risky = data["risky_rules"]
    if risky:
        reasons = "; ".join(r["reason"] for r in risky)
        detail = f"{len(risky)} overly-permissive security-group rule(s) on {node['hostname']}: {reasons}."
        return {"has_signal": True, "degraded": False, "detail": detail, "data": data, "risky_rules": risky}

    if not data["security_groups"]:
        detail = f"No security groups attached to any instance on {node['hostname']}."
    else:
        detail = f"{len(data['security_groups'])} security group(s) checked on {node['hostname']}, no overly-permissive ingress rules found."
    return {"has_signal": False, "degraded": False, "detail": detail, "data": data, "risky_rules": []}


# --------------------------------------------------------------------
# Sub-check 3: CVE-match (package inventory)
# --------------------------------------------------------------------

def _check_cve_match(node: KnownNode) -> dict:
    breaker = get_breaker("security.cve_feed", timeout_seconds=8.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(cve_feed.get_package_inventory, node["hostname"])

    if not call_result.ok:
        logger.warning("security_agent: CVE-match sub-check failed: %s", call_result.failure)
        return {
            "has_signal": False,
            "degraded": True,
            "failure": call_result.failure,
            "detail": (
                "The package-inventory check couldn't complete, so known-vulnerable packages on "
                "this host are unknown rather than confirmed absent."
            ),
            "matches": [],
        }

    matches = cve_feed.match_cves(call_result.value)
    if not matches:
        return {
            "has_signal": False,
            "degraded": False,
            "detail": f"No known-vulnerable package versions found on {node['hostname']}.",
            "matches": [],
        }

    worst = max(matches, key=lambda m: {"critical": 3, "high": 2, "medium": 1, "low": 0}.get(m["severity"], 0))
    detail = (
        f"{len(matches)} known-vulnerable package(s) on {node['hostname']}, worst: {worst['cve_id']} "
        f"({worst['severity']}) in {worst['package']} {worst['installed_version']} -- fixed in {worst['fixed_version']}."
    )
    return {"has_signal": True, "degraded": False, "detail": detail, "matches": matches}


# --------------------------------------------------------------------
# Sub-check 4: eBPF kernel-level signal
# --------------------------------------------------------------------

def _check_ebpf_signal(node: KnownNode) -> dict:
    breaker = get_breaker("security.ebpf", timeout_seconds=8.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(ebpf_signal.get_node_ebpf_alerts, node["hostname"])

    if not call_result.ok:
        logger.warning("security_agent: eBPF-signal sub-check failed: %s", call_result.failure)
        return {
            "has_signal": False,
            "degraded": True,
            "failure": call_result.failure,
            "detail": (
                "The kernel-level (eBPF) check couldn't complete -- no sensor reachable, so "
                "runtime behavior on this host is unknown rather than confirmed clean."
            ),
            "alerts": [],
        }

    alerts = call_result.value
    if not alerts:
        return {
            "has_signal": False,
            "degraded": False,
            "detail": f"No active kernel-level alerts for {node['hostname']}.",
            "alerts": [],
        }

    top = alerts[0]
    detail = (
        f"{len(alerts)} active kernel-level alert(s) for {node['hostname']}, most severe: "
        f"[{top.get('priority')}] {top.get('rule')} -- {top.get('output')}"
    )
    return {"has_signal": True, "degraded": False, "detail": detail, "alerts": alerts[:5]}


# --------------------------------------------------------------------
# Merge: four sub-check results -> one AgentResult
# --------------------------------------------------------------------

def _confidence(auth: dict, sec_group: dict, cve: dict, ebpf: dict) -> float:
    if ebpf["has_signal"]:
        base = _EBPF_SIGNAL_CONFIDENCE
    elif sec_group["has_signal"] or cve["has_signal"]:
        base = _CONFIG_SIGNAL_CONFIDENCE
    elif auth["has_signal"]:
        base = _AUTH_LOG_ONLY_CONFIDENCE
    else:
        base = _NO_SIGNAL_CONFIDENCE

    if any(sig.get("degraded") for sig in (auth, sec_group, cve, ebpf)):
        # Same idiom as anomaly.py/network.py's degraded caps: a sub-check
        # that couldn't run means "unknown", not "confirmed clean" --
        # worth less than a signal that actually fired, but not worth
        # penalizing like a checked-and-found-nothing result would be.
        base = min(base, _DEGRADED_CONFIDENCE_CAP)

    return round(base, 2)


_SYSTEM_PROMPT = """You are Cortex's security investigation assistant. You're given four independent \
readings for one node: auth-failure log activity (Loki), a security-group audit (Neutron, flagging \
overly-permissive ingress rules), a known-vulnerable-package check (CVE match against installed \
versions), and kernel-level runtime alerts (eBPF/Falco-style). Write a developed security finding, \
4-6 sentences, covering: what each signal that fired actually shows (name specifics -- which CVE, which \
port/rule, which alert -- when given), which signal should be weighted most heavily (a kernel-level/eBPF \
signal is the strongest evidence here since it reflects actual observed behavior, not just \
configuration or log correlation) and why, and a concrete next step (which log/rule/package/alert to \
check first). Use ONLY the evidence given -- never invent a CVE id, rule, or alert that wasn't provided. \
If nothing fired across all four checks, say so plainly and don't force a finding."""


def _fallback_summary(node: KnownNode, auth: dict, sec_group: dict, cve: dict, ebpf: dict) -> str:
    signals = [auth, sec_group, cve, ebpf]
    if not any(s["has_signal"] for s in signals) and not any(s.get("degraded") for s in signals):
        return (
            f"No security signal on {node['hostname']}: auth logs are clean, no overly-permissive "
            "security-group rules, no known-vulnerable packages, and no active kernel-level alerts."
        )
    parts = [s["detail"] for s in signals]
    return " ".join(parts)


def _narrate(query: str, node: KnownNode, auth: dict, sec_group: dict, cve: dict, ebpf: dict) -> str:
    fallback = _fallback_summary(node, auth, sec_group, cve, ebpf)
    try:
        llm = get_chat_model(temperature=0.2, tier="reasoning")
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Question: {query}\n\n"
                        f"Node: {node['hostname']} (role: {node['role']})\n"
                        f"Auth-anomaly (log-based): {auth['detail']}\n"
                        f"Sec-group-diff (config): {sec_group['detail']}\n"
                        f"CVE-match (config): {cve['detail']}\n"
                        f"eBPF signal (kernel/behavioral): {ebpf['detail']}"
                    )
                ),
            ]
        )
        text = (response.content or "").strip()
        return text or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("security_agent: LLM narration failed, using fallback summary")
        return fallback


def _investigate(query: str, node: KnownNode) -> tuple[AgentResult, list]:
    """Runs all four sub-checks and returns (AgentResult, embedded_failures)
    without touching any graph state -- mirrors anomaly.py's `_investigate`
    exactly, for the same reason: both the standalone leaf path
    (security_agent) and the fan-out Send target
    (security_investigate_one) need it, kept in one place so they can't
    drift apart."""
    auth = _check_auth_anomaly(node)
    sec_group = _check_sec_group_diff(node)
    cve = _check_cve_match(node)
    ebpf = _check_ebpf_signal(node)

    summary = _narrate(query, node, auth, sec_group, cve, ebpf)
    confidence = _confidence(auth, sec_group, cve, ebpf)
    has_signal = any(s["has_signal"] for s in (auth, sec_group, cve, ebpf))

    agent_result: AgentResult = {
        "summary": summary,
        "confidence": confidence,
        "raw_data": {
            "hostname": node["hostname"],
            "role": node["role"],
            "has_signal": has_signal,
            "auth_signal": auth,
            "sec_group_signal": sec_group,
            "cve_signal": cve,
            "ebpf_signal": ebpf,
        },
    }

    failures = [s["failure"] for s in (auth, sec_group, cve, ebpf) if s.get("degraded") and s.get("failure")]
    return agent_result, failures


def security_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    node = resolve_node(state["user_query"], known_nodes, session_memory=state.get("session_memory"))

    if node is None:
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = f"I couldn't tell which node you meant. Known nodes: {available}."
        state["agent_result"] = None
        return state

    agent_result, failures = _investigate(state["user_query"], node)

    state["agent_result"] = agent_result
    state["error"] = None
    for failure in failures:
        state.setdefault("failures", []).append(failure)
    state.setdefault("resolved_entities", {})["last_node"] = node
    state["resolved_entities"]["last_agent"] = "security"
    return state


# --------------------------------------------------------------------
# v0.9: fan-out Send target -- one branch of the multi-agent incident
# investigation (see graph.py's `_fan_out_to_investigate` and
# nodes/anomaly.py's `anomaly_arbitrate`, which is what actually joins
# this back together with anomaly's and network's own findings).
# --------------------------------------------------------------------

def _security_investigate_one_impl(payload: dict) -> dict:
    query = payload["user_query"]
    node = payload["node"]
    agent_result, failures = _investigate(query, node)
    finding: IncidentFinding = {
        "hostname": node["hostname"],
        "agent": "security",
        "agent_result": agent_result,
        "failures": failures,
    }
    return {"agent_results": [finding]}


security_investigate_one = guarded_send("security.investigate", timeout_seconds=60.0)(_security_investigate_one_impl)
