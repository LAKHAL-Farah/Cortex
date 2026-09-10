"""Anomaly agent -- incident-investigation questions ("something's wrong
with compute-01", "why is X acting up", "investigate this alert") (v0.4).

Every other agent in this package (monitoring, prediction, rag) pulls from
exactly one data source and narrates it. Investigating an incident isn't a
single-source read: a metric reading above/flagged as anomalous is only
half a finding until you know whether anything else corroborates it, so
this is the first agent that needs *internal sub-orchestration* -- two
independent sub-steps that each gather one kind of evidence, run inside
this single graph node, and get merged into one AgentResult before the
graph ever sees them:

1. `_check_metrics` -- is there a live/flagged metric signal for this
   node? Prefers the anomaly detector's own scored output (AnomalyFlag,
   the same table GET /api/v1/anomalies reads -- already median/MAD-scored
   against a real baseline, see services/anomaly_detector.py) over
   recomputing anything. Falls back to a live Prometheus threshold read
   (same collect_metrics() the monitoring agent uses) only when no flag
   exists yet, e.g. right after a spike before the detector's next tick.
2. `_check_logs` -- are there correlated error/warning log lines around
   the same window? Queries Loki directly (loki_client, same client
   routers/logs.py uses), windowed around the metric signal's
   detected_at when there is one, so "correlated" means something
   temporally, not just "any recent error on that host".

v0.5 (adr-0007) changes how `_check_logs` fails: it already caught a
Loki error and degraded to "no signal" rather than crashing, but that made
"Loki is down" and "Loki answered and there were no correlated lines"
produce an identical `has_signal: False` -- compose.py, the confidence
score, and the narrative all had no way to tell "we don't know" from "we
checked, and it's clean". The query now goes through
`resilience.get_breaker("anomaly.loki")` (hard timeout + one retry +
circuit-opens-after-repeated-failures, see resilience.py) and, on failure,
the log signal is tagged `degraded: True` with its `FailureRecord`
attached -- `_confidence` caps rather than penalizes a degraded signal
(we're missing information, not confirming an absence), the narrative says
plainly that the log-check couldn't complete, and `anomaly_agent` pushes
the FailureRecord into `state["failures"]` so compose.py's aggregation
step labels the whole answer as degraded too.

A third step, `_hypothesize_cause`, doesn't gather new evidence -- it
pattern-matches the *content* of what the first two steps already found
(specific log keywords like "OOM"/"no space left"/"lost connection to
libvirt", or which resource is the one reading hot) against a small table
of common infra failure signatures, to turn "here are two signals" into
"here's what this probably is". This is offered to the narrator (LLM or
fallback) as a hedged hypothesis, never as a confirmed diagnosis -- it's a
starting point for whoever's investigating, not a verdict.

compose.py's aggregation/arbitration step downstream stays intentionally
trivial (docstring there) -- with exactly one investigating agent right
now, "arbitration" *is* presenting this merged finding. The sub-
orchestration pattern is built once, here, so the Security Agent (planned
to reuse this exact shape) doesn't have to reinvent it.

v0.8 (efficiency & scale prep) adds dynamic multi-node fan-out on top of
the single-node investigation above, without changing that investigation
itself: `_investigate` (the shared metric-check -> log-check ->
hypothesize -> narrate -> confidence pipeline) is now a plain function
reused by both paths --

- `anomaly_agent` -- unchanged: resolves exactly one node and calls
  `_investigate` directly. Still what's exercised when this module's
  functions are called directly (see test_anomaly_agent.py).
- `anomaly_dispatch` / `anomaly_investigate_one` / `anomaly_arbitrate` --
  the graph-level path (see graph.py), used when a question doesn't name
  one specific node at all (e.g. "is anything wrong right now"). Instead
  of a fixed/hardcoded set of nodes to check, `anomaly_dispatch` scopes
  the investigation to whichever nodes the *Living Model* -- the current
  topology (`known_nodes`) intersected with whichever of them currently
  have an open AnomalyFlag -- says are actually worth looking at, then
  fans out via LangGraph's `Send` primitive: one `anomaly_investigate_one`
  invocation per node, running concurrently rather than in a loop, so
  investigating N nodes doesn't cost N times the latency of investigating
  one. `anomaly_arbitrate` is where those N independent findings actually
  get arbitrated -- ranked by confidence, with a reasoning-tier LLM call
  synthesizing a cross-node incident narrative when there's more than one
  (skipped, at zero added cost, for the common single-node case) -- this
  is the "arbitration" this module's v0.4 docstring above deferred to the
  day a second source of findings existed; a second *node*, not just a
  second *agent*, turned out to be the first one to actually need it.
"""
import logging
import re
import time
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from ...db import SessionLocal
from ... import crud
from ...services import loki_client
from ...services.llm_client import LLMConfigError, get_chat_model
from ...services.metrics_collector import collect_metrics
from ..node_resolver import resolve_node
from ..resilience import get_breaker, guarded_send
from ..state import AgentResult, CortexState, IncidentFinding, KnownNode

logger = logging.getLogger(__name__)

# v0.8 dynamic fan-out: a broad "is anything wrong" question (no specific
# node named) scopes the investigation to nodes the Living Model currently
# flags, rather than every known node -- catches the common phrasings
# without trying to be an exhaustive intent classifier (that's the
# router's job; this only fires once node resolution has already failed to
# find one specific node in the question).
_BROAD_INCIDENT_PATTERN = re.compile(
    r"(?i)anything wrong|what'?s wrong|what is wrong|any(?:thing)? (?:down|broken|failing)|"
    r"status of everything|across (?:the|all|our) (?:nodes|cluster|fleet)|"
    r"everything (?:ok|okay|fine)|any (?:incidents?|issues?|alerts?)|current incidents?"
)
# Bounds how many nodes one turn will fan out to -- a real incident sweep
# should surface the handful of nodes actually flagged, not silently
# balloon fan-out cost/latency on a fleet with dozens of open flags.
_MAX_INCIDENT_FANOUT = 5

# How anomaly_detector.py ranks severity, duplicated here (rather than
# imported) because that module's _SEVERITY_RANK is private -- this agent
# only needs it to pick the worst of possibly-several open flags for one
# host, not to touch anomaly_detector's detection logic itself.
_SEVERITY_RANK = {"normal": 0, "medium": 1, "high": 2, "critical": 3}

# A scored AnomalyFlag (real baseline behind it) is trusted more than a
# bare "is this over 90%" live read -- confidence reflects that.
_SEVERITY_CONFIDENCE = {"critical": 0.95, "high": 0.85, "medium": 0.7}
_LIVE_THRESHOLD_CONFIDENCE = 0.55
_NO_METRIC_SIGNAL_CONFIDENCE = 0.2

# A degraded log-check (Loki unreachable, see _check_logs) means "we don't
# know" -- worth less than a confirmed corroboration but not worth the same
# penalty as "we checked and found nothing", since the latter is itself
# evidence and the former isn't. Applied as a cap, not an offset, so it
# never accidentally raises a low metric-only confidence.
_DEGRADED_LOG_CONFIDENCE_CAP = 0.6

# How far around the metric signal's detection time (or "now", if there is
# no timestamped signal to anchor on) the log-check sub-step looks for
# corroborating lines.
_LOG_WINDOW_MINUTES = 30

# Loose net on purpose: this is a first-pass correlation signal for the
# narrative, not a log classifier. Broad enough to catch OpenStack/service
# log conventions ("ERROR", "WARN", a stack "Exception", "Timeout") without
# an LLM in the loop for this sub-step -- the numbers/lines it surfaces are
# real Loki output, never invented.
_LOG_SIGNAL_PATTERN = "(?i)error|warn|fail|exception|timeout|refused|unreachable"


def _iso_utc(dt) -> str | None:
    """Same fix as routers/anomalies.py's _iso_utc: detected_at is stored
    naive UTC (datetime.utcnow()), so attach tzinfo explicitly before
    formatting or a naive isoformat() string gets misread as local time by
    anything that later parses it."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _escape_logql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# --------------------------------------------------------------------
# Sub-step 1: metric-check
# --------------------------------------------------------------------

def _metric_signal_from_flags(hostname: str) -> dict | None:
    """Tier 1: the anomaly detector's own scored output, if it's flagged
    this host. Opens its own short-lived session (same pattern
    routers/nodes.py's background cleanup uses) rather than threading a
    live DB Session through graph state -- state has to stay
    JSON-serializable (see state.py's module docstring)."""
    db = SessionLocal()
    try:
        flags = crud.list_open_anomaly_flags(db, hostname)
    finally:
        db.close()

    if not flags:
        return None

    worst = max(flags, key=lambda f: _SEVERITY_RANK.get(f.severity, 0))
    return {
        "source": "anomaly_flags",
        "metric_name": worst.metric_name,
        "current_value": worst.current_value,
        "z_score": worst.z_score,
        "severity": worst.severity,
        "method": worst.method,
        "detected_at": _iso_utc(worst.detected_at),
        "other_flagged_metrics": [f.metric_name for f in flags if f is not worst],
    }


def _metric_signal_from_live(node) -> dict | None:
    """Tier 2 fallback: a live Prometheus read, same source and thresholds
    the monitoring agent uses. Only consulted when no AnomalyFlag exists
    yet for this host -- e.g. right after a spike, before the detector's
    next periodic tick has scored it."""
    try:
        live_by_instance = {m["instance"]: m for m in collect_metrics()}
    except Exception:
        logger.exception("anomaly_agent: metric-check live fallback failed")
        return None

    metrics = live_by_instance.get(node["instance"])
    if metrics is None:
        return None
    if metrics["status"] == "up" and metrics["health"] == "healthy":
        return None

    return {
        "source": "live_metrics",
        "cpu_percent": metrics["cpu_percent"],
        "memory_percent": metrics["memory_percent"],
        "disk_percent": metrics["disk_percent"],
        "status": metrics["status"],
        "health": metrics["health"],
    }


def _check_metrics(node) -> dict:
    signal = _metric_signal_from_flags(node["hostname"])
    if signal is not None:
        detail = (
            f"{node['hostname']}'s {signal['metric_name'].replace('_', ' ')} is flagged "
            f"{signal['severity']} by the anomaly detector (z={signal['z_score']:.1f}, "
            f"current value {signal['current_value']:.1f})."
        )
        return {"has_signal": True, "detail": detail, "data": signal}

    live = _metric_signal_from_live(node)
    if live is not None:
        detail = (
            f"{node['hostname']} is currently reading {live['health']} live "
            f"(CPU {live['cpu_percent']}%, RAM {live['memory_percent']}%, "
            f"disk {live['disk_percent']}%, status {live['status']}), though nothing "
            "flagged yet by the anomaly detector."
        )
        return {"has_signal": True, "detail": detail, "data": live}

    return {
        "has_signal": False,
        "detail": f"No metric anomaly currently flagged or reading abnormal for {node['hostname']}.",
        "data": None,
    }


# --------------------------------------------------------------------
# Sub-step 2: log-check
# --------------------------------------------------------------------

def _log_window(metric_signal: dict) -> tuple[float, float]:
    now = time.time()
    detected_iso = (metric_signal.get("data") or {}).get("detected_at")
    anchor = now
    if detected_iso:
        try:
            anchor = datetime.fromisoformat(detected_iso).timestamp()
        except ValueError:
            anchor = now

    pad = _LOG_WINDOW_MINUTES * 60
    return anchor - pad, min(anchor + pad, now)


def _check_logs(node, metric_signal: dict) -> dict:
    start, end = _log_window(metric_signal)
    logql = f'{{host="{_escape_logql(node["hostname"])}"}} |~ "{_LOG_SIGNAL_PATTERN}"'

    breaker = get_breaker("anomaly.loki", timeout_seconds=8.0, max_retries=1, failure_threshold=2)
    call_result = breaker.call(loki_client.query_range, logql, start, end, limit=50)

    if not call_result.ok:
        logger.warning("anomaly_agent: log-check sub-step failed to query Loki: %s", call_result.failure)
        return {
            "has_signal": False,
            "degraded": True,
            "failure": call_result.failure,
            "detail": (
                "The log-check couldn't complete (the log store didn't respond in time), so "
                "this finding is metric-only with reduced confidence -- log correlation "
                "wasn't ruled in or out."
            ),
            "entries": [],
        }

    streams = call_result.value
    entries = []
    for stream in streams:
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entries.append({
                "ts": int(ts_ns) // 1_000_000,
                "line": line,
                "service": labels.get("service") or labels.get("job"),
            })
    entries.sort(key=lambda e: e["ts"], reverse=True)

    if not entries:
        return {
            "has_signal": False,
            "degraded": False,
            "detail": (
                f"No correlated error/warning log entries found for {node['hostname']} "
                "in the surrounding window."
            ),
            "entries": [],
        }

    top = entries[:5]
    plural = "y" if len(entries) == 1 else "ies"
    detail = (
        f"{len(entries)} correlated log entr{plural} found for {node['hostname']}, most "
        f"recent: \"{top[0]['line'][:160]}\"."
    )
    return {"has_signal": True, "degraded": False, "detail": detail, "entries": top}


# --------------------------------------------------------------------
# Sub-step 3 (derived, not gathered): cause hypothesis
# --------------------------------------------------------------------
# Not a new data source -- this reads the content of what _check_metrics
# and _check_logs already found and matches it against a small table of
# common infra failure signatures. Log content wins when it's available
# (it's the more specific signal); a metric-only signal only gets a
# generic "which resource" guess. Always surfaced as a hypothesis, never
# a diagnosis -- see _SYSTEM_PROMPT / _fallback_summary for how it's
# hedged in the actual narrative.

_LOG_CAUSE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)out\s*of\s*memory|oom[-_ ]?kill|memoryerror"),
     "a memory leak or a runaway process consuming RAM"),
    (re.compile(r"(?i)no space left|enospc|disk full|filesystem is full"),
     "the disk filling up -- runaway log/data growth, a stuck write, or an unrelated process eating disk space"),
    (re.compile(r"(?i)lost connection to (the )?(libvirt|hypervisor)|qemu[-_ ]?kvm|libvirtd"),
     "a hypervisor/libvirt problem affecting one or more VMs on this host"),
    (re.compile(r"(?i)connection refused|connect(ion)? timed?\s*out|unreachable|no route to host"),
     "a dependent service being down or unreachable, or a network partition"),
    (re.compile(r"(?i)traceback|unhandled exception|panic:|segfault|core dumped"),
     "an application crash or an unhandled exception in a running service"),
    (re.compile(r"(?i)restart(ing|ed)? (unexpectedly|repeatedly)|crash loop|respawn(ing)?"),
     "a service stuck in a crash-restart loop"),
    (re.compile(r"(?i)too many open files|ulimit|file descriptor"),
     "a file-descriptor leak or a resource limit being hit"),
]

# Metric-only guesses when there's a signal but nothing in the logs
# narrows it down -- deliberately vaguer than the log-based hypotheses
# above, since a bare metric reading doesn't say *why* it's elevated.
_METRIC_ONLY_HINTS = {
    "cpu_usage": "a runaway or stuck process pinning the CPU",
    "ram_usage": "a memory leak or a process that isn't releasing memory it no longer needs",
}
_LIVE_METRIC_HINT_THRESHOLD = 85.0


def _hypothesize_cause(metric_signal: dict, log_signal: dict) -> str | None:
    if log_signal["has_signal"]:
        for entry in log_signal["entries"]:
            for pattern, hypothesis in _LOG_CAUSE_PATTERNS:
                if pattern.search(entry["line"]):
                    return hypothesis

    if not metric_signal["has_signal"]:
        return None

    data = metric_signal["data"] or {}
    if data.get("source") == "anomaly_flags":
        return _METRIC_ONLY_HINTS.get(data.get("metric_name"))

    # Live-threshold tier: guess based on whichever of CPU/RAM/disk is
    # actually reading hot, since there's no single "metric_name" here.
    candidates = [
        (data.get("cpu_percent", 0.0), "CPU", "a runaway or stuck process pinning the CPU"),
        (data.get("memory_percent", 0.0), "memory", "a memory leak or a process not releasing RAM"),
        (data.get("disk_percent", 0.0), "disk", "something filling up the disk faster than expected"),
    ]
    value, _resource, hint = max(candidates, key=lambda c: c[0])
    return hint if value >= _LIVE_METRIC_HINT_THRESHOLD else None


# --------------------------------------------------------------------
# Merge: two sub-step results -> one AgentResult
# --------------------------------------------------------------------

def _confidence(metric_signal: dict, log_signal: dict) -> float:
    if not metric_signal["has_signal"]:
        base = _NO_METRIC_SIGNAL_CONFIDENCE
    else:
        data = metric_signal["data"] or {}
        if data.get("source") == "anomaly_flags":
            base = _SEVERITY_CONFIDENCE.get(data.get("severity"), _LIVE_THRESHOLD_CONFIDENCE)
        else:
            base = _LIVE_THRESHOLD_CONFIDENCE

    if log_signal.get("degraded"):
        # We don't know whether logs would have corroborated this or not --
        # cap rather than penalize, since "the check failed" isn't evidence
        # of absence the way "the check ran clean" is.
        base = min(base, _DEGRADED_LOG_CONFIDENCE_CAP)
    elif log_signal["has_signal"]:
        # Corroborating evidence from an independent source -- raise
        # confidence, capped just under certain (nothing here is 100%
        # verified root cause, just two aligned signals).
        base = min(0.97, base + 0.15)
    elif metric_signal["has_signal"]:
        # A metric signal with nothing corroborating it in the logs is
        # less certain than one that lines up with an error trail.
        base = max(0.3, base - 0.1)

    return round(base, 2)


_SYSTEM_PROMPT = """You are Cortex's incident investigation assistant. You're given evidence gathered \
for one node: a metric signal (from live Prometheus data or the anomaly detector), a log signal \
(correlated error/warning log lines from Loki), and sometimes a candidate-cause hint (a heuristic \
guess, not a confirmed diagnosis). Write a developed incident finding, 4-6 sentences, covering all of:

1. What the metric signal shows -- name the metric, its severity/value/z-score if given.
2. Whether the logs corroborate it, and specifically what they say (quote or closely paraphrase the \
most relevant line if one was given).
3. Your best hypothesis for the underlying root cause, clearly hedged as a hypothesis, not a confirmed \
diagnosis (e.g. "this pattern is consistent with...", "a plausible explanation is..."). Use the \
candidate-cause hint if one is given, but sharpen, refine, or override it if the actual log content \
suggests something more specific -- the hint is a starting point, not the answer.
4. A concrete, specific next diagnostic step: what to check on the host, which process/service to \
inspect, or what to pull from the logs next.

Use ONLY the evidence given -- never invent a number, a log line, or a fact that wasn't provided. If \
there's no metric or log evidence at all, say so plainly in 1-2 sentences and don't force a hypothesis \
or a next step that isn't warranted."""


def _fallback_summary(node, metric_signal: dict, log_signal: dict, likely_cause: str | None) -> str:
    hostname = node["hostname"]
    degraded = log_signal.get("degraded", False)

    if not metric_signal["has_signal"] and not log_signal["has_signal"] and not degraded:
        return (
            f"No current metric or log evidence of an incident on {hostname}. The anomaly detector "
            f"isn't flagging anything, a live Prometheus read looks normal, and a scan of recent "
            f"error/warning logs for {hostname} came back clean. Nothing here points to an active "
            "problem right now -- worth re-checking if symptoms are still being reported elsewhere."
        )

    if not metric_signal["has_signal"] and degraded:
        return (
            f"No metric anomaly currently flagged or reading abnormal for {hostname}, and the "
            "log-check couldn't complete (the log store didn't respond in time) -- so this isn't "
            "a clean bill of health, it's an incomplete one. Worth re-running once the log store "
            "is reachable again, or checking it directly in the meantime."
        )

    sentences = [metric_signal["detail"], log_signal["detail"]]

    both_signals = metric_signal["has_signal"] and log_signal["has_signal"]
    if likely_cause:
        if both_signals:
            sentences.append(
                f"Taken together, this pattern is consistent with {likely_cause} -- a reasonable leading "
                "hypothesis given the evidence, though it isn't confirmed without checking the host directly."
            )
        else:
            sentences.append(
                f"With only one of the two signals present, the evidence is thinner, but if this does turn "
                f"out to be a real incident, {likely_cause} would be a reasonable starting hypothesis."
            )
    else:
        sentences.append(
            "Nothing in the evidence gathered points to a specific root cause yet, so this is worth treating "
            "as an open question rather than guessing."
        )

    if log_signal["has_signal"]:
        sentences.append(
            f"Recommended next step: pull the full log stream around this window for {hostname} "
            "(the correlated lines above are a sample, not the complete picture) and check what "
            "process or service is driving the metric signal on the host itself."
        )
    elif degraded:
        sentences.append(
            f"Recommended next step: check {hostname} directly and retry the log-check once the "
            "log store is reachable again -- the metric signal alone is worth investigating, but "
            "log correlation genuinely hasn't been ruled in or out here."
        )
    elif metric_signal["has_signal"]:
        sentences.append(
            f"Recommended next step: since no corroborating logs turned up, check {hostname} directly "
            "-- current process/resource usage on the host -- to see whether this is a real, ongoing "
            "issue or a transient spike."
        )

    return " ".join(sentences)


def _narrate(query: str, node, metric_signal: dict, log_signal: dict, likely_cause: str | None) -> str:
    fallback = _fallback_summary(node, metric_signal, log_signal, likely_cause)
    try:
        llm = get_chat_model(temperature=0.2, tier="reasoning")
        cause_line = (
            f"Candidate cause hint (heuristic, not confirmed): {likely_cause}"
            if likely_cause
            else "Candidate cause hint: none matched -- form your own hypothesis only if the evidence supports one."
        )
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Question: {query}\n\n"
                        f"Node: {node['hostname']} (role: {node['role']})\n"
                        f"Metric signal: {metric_signal['detail']}\n"
                        f"Log signal: {log_signal['detail']}\n"
                        f"{cause_line}"
                    )
                ),
            ]
        )
        text = (response.content or "").strip()
        return text or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("anomaly_agent: LLM narration failed, using fallback summary")
        return fallback


# --------------------------------------------------------------------
# Shared single-node pipeline (v0.8: factored out of anomaly_agent so the
# dynamic fan-out path below can reuse it without duplicating the
# metric-check -> log-check -> hypothesize -> narrate -> confidence chain)
# --------------------------------------------------------------------

def _investigate(query: str, node: KnownNode) -> tuple[AgentResult, list]:
    """Runs the full single-node investigation pipeline and returns
    (AgentResult, embedded_failures) without touching any graph state --
    the one thing both anomaly_agent (single-node, direct call) and
    anomaly_investigate_one (one Send branch of a multi-node fan-out) need,
    kept in exactly one place so they can never drift apart."""
    metric_signal = _check_metrics(node)
    log_signal = _check_logs(node, metric_signal)
    likely_cause = _hypothesize_cause(metric_signal, log_signal)

    summary = _narrate(query, node, metric_signal, log_signal, likely_cause)
    confidence = _confidence(metric_signal, log_signal)

    agent_result: AgentResult = {
        "summary": summary,
        "confidence": confidence,
        "raw_data": {
            "hostname": node["hostname"],
            "role": node["role"],
            "metric_signal": metric_signal,
            "log_signal": log_signal,
            "likely_cause": likely_cause,
        },
    }

    failures = []
    if log_signal.get("degraded") and log_signal.get("failure"):
        failures.append(log_signal["failure"])
    return agent_result, failures


def anomaly_agent(state: CortexState) -> CortexState:
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
        # A degraded sub-step doesn't stop this agent from producing a
        # usable result (that's the point), but compose.py still needs to
        # know evidence-gathering partially failed so it can label the
        # answer honestly -- see compose.py's module docstring.
        state.setdefault("failures", []).append(failure)
    state.setdefault("resolved_entities", {})["last_node"] = node
    state["resolved_entities"]["last_agent"] = "anomaly"
    return state


# --------------------------------------------------------------------
# v0.8: dynamic multi-node fan-out (dispatch -> Send x N -> arbitrate)
# --------------------------------------------------------------------

def _incident_scope_from_living_model(query: str, known_nodes: list[KnownNode]) -> list[KnownNode]:
    """Scopes a broad, no-specific-node incident question to whichever
    known nodes currently have an open AnomalyFlag -- the Living Model
    (the topology graph's current node list, intersected with the anomaly
    detector's live output), not a fixed/hardcoded set. Returns [] for
    anything that doesn't read as a broad incident question at all (the
    caller falls through to the existing "couldn't tell which node"
    clarify path in that case, same as before this existed)."""
    if not _BROAD_INCIDENT_PATTERN.search(query):
        return []

    db = SessionLocal()
    try:
        flagged_hostnames = crud.list_all_open_anomaly_flag_hostnames(db)
    finally:
        db.close()

    known_by_hostname = {n["hostname"]: n for n in known_nodes}
    scope = [known_by_hostname[h] for h in flagged_hostnames if h in known_by_hostname]
    return scope[:_MAX_INCIDENT_FANOUT]


def anomaly_dispatch(state: CortexState) -> CortexState:
    """Sequential entry point for the fan-out path (see graph.py). Decides
    *which* nodes this turn investigates and writes that list into
    `state["incident_scope"]` for the conditional edge right after this
    node to turn into one Send per node -- this function itself never
    calls an agent, it only resolves scope, so it's cheap and fast-tier
    (node_resolver's own LLM tier) even though what follows may fan out to
    several reasoning-tier investigations.
    """
    known_nodes = state["known_nodes"]
    query = state["user_query"]

    node = resolve_node(query, known_nodes, session_memory=state.get("session_memory"))
    if node is not None:
        state["incident_scope"] = [node]
        state["error"] = None
        return state

    scope = _incident_scope_from_living_model(query, known_nodes)
    if scope:
        state["incident_scope"] = scope
        state["error"] = None
        return state

    available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
    state["incident_scope"] = []
    state["agent_result"] = None
    state["error"] = f"I couldn't tell which node you meant. Known nodes: {available}."
    return state


def _anomaly_investigate_one_impl(payload: dict) -> dict:
    """The actual per-node work behind one Send("anomaly_investigate", ...)
    branch. Deliberately takes a narrow `payload` (query + one node), not
    CortexState -- see resilience.guarded_send's docstring for why a Send
    target can't work off the graph's full state the way every other node
    here does."""
    query = payload["user_query"]
    node = payload["node"]
    agent_result, failures = _investigate(query, node)
    finding: IncidentFinding = {
        "hostname": node["hostname"],
        "agent_result": agent_result,
        "failures": failures,
    }
    return {"agent_results": [finding]}


# TEMP (2026-09-04, revised): 60s (not None) -- see graph.py's build_graph
# for why. _narrate() inside this call is what hits the LLM. Put 30.0 back
# once NIM's current instability is resolved.
anomaly_investigate_one = guarded_send("anomaly.investigate", timeout_seconds=60.0)(_anomaly_investigate_one_impl)


_ARBITRATION_SYSTEM_PROMPT = """You are Cortex's incident investigation assistant. You're given \
independent findings for several nodes, gathered because they're all part of the same broad incident \
question. Write a short (3-5 sentence) cross-node incident summary: which node(s) look most serious \
and why, whether the findings look related (e.g. the same likely cause recurring, or one node's \
symptom plausibly cascading to another) or look like separate unrelated issues, and what to check or \
prioritize first. Use ONLY the findings given -- never invent a node, a number, or a detail that \
wasn't provided."""


def _arbitrate_narrative(query: str, ranked: list[IncidentFinding]) -> str:
    fallback = " ".join(
        f"{f['hostname']} (confidence {f['agent_result']['confidence']:.2f}): {f['agent_result']['summary']}"
        for f in ranked
    )
    try:
        llm = get_chat_model(temperature=0.2, tier="reasoning")
        findings_text = "\n\n".join(
            f"Node: {f['hostname']}\nConfidence: {f['agent_result']['confidence']:.2f}\n"
            f"Finding: {f['agent_result']['summary']}"
            for f in ranked
        )
        response = llm.invoke(
            [
                SystemMessage(content=_ARBITRATION_SYSTEM_PROMPT),
                HumanMessage(content=f"Question: {query}\n\n{findings_text}"),
            ]
        )
        text = (response.content or "").strip()
        return text or fallback
    except LLMConfigError:
        return fallback
    except Exception:
        logger.exception("anomaly_arbitrate: LLM arbitration narrative failed, using fallback summary")
        return fallback


def anomaly_arbitrate(state: CortexState) -> CortexState:
    """Converges the fan-out back into the single agent_result shape every
    downstream node (should_trigger_after_anomaly, critic, compose)
    already expects. Ranks findings by confidence, keeps the worst/most-
    urgent one as `agent_result`'s primary raw_data shape (so chaining
    into openstack_expert keeps working unchanged for the common case),
    and -- only when there's more than one finding, so the single-node
    case costs exactly what it always did -- adds a `multi_node_findings`
    breakdown plus a reasoning-tier cross-node narrative.

    Also the one place `state["agent_results"]` gets consumed: this node
    runs sequentially after the fan-out has fully joined and reads that
    field to do its ranking/merging. It's safe to just read it (no need
    to clear it afterward) precisely because state.py's `_concat` reducer
    dedupes by hostname -- see that function's docstring for why a plain
    concatenating reducer would otherwise silently double the fan-out's
    findings at every downstream node instead.
    """
    findings: list[IncidentFinding] = state.get("agent_results") or []

    if not findings:
        # anomaly_dispatch already set state["error"] (no scope resolved)
        # -- nothing to arbitrate, same as the old "couldn't tell which
        # node" short-circuit.
        return state

    for finding in findings:
        for failure in finding.get("failures") or []:
            state.setdefault("failures", []).append(failure)

    ranked = sorted(findings, key=lambda f: f["agent_result"]["confidence"], reverse=True)
    primary = ranked[0]
    merged_raw = dict(primary["agent_result"]["raw_data"])

    if len(ranked) > 1:
        merged_raw["multi_node_findings"] = [
            {
                "hostname": f["hostname"],
                "confidence": f["agent_result"]["confidence"],
                "summary": f["agent_result"]["summary"],
            }
            for f in ranked
        ]
        summary = _arbitrate_narrative(state["user_query"], ranked)
    else:
        summary = primary["agent_result"]["summary"]

    state["agent_result"] = {
        "summary": summary,
        "confidence": primary["agent_result"]["confidence"],
        "raw_data": merged_raw,
    }
    state["error"] = None
    state.setdefault("resolved_entities", {})["last_node"] = {"hostname": primary["hostname"]}
    state["resolved_entities"]["last_agent"] = "anomaly"
    return state
