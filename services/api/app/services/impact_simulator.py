"""Impact simulator (roadmap 4.2): "proposed fix simulated against the Living
Model, shows expected impact before approval" (see
docs/architecture/adr-0013-impact-simulation.md).

The Remediation Agent (nodes/remediation.py, 4.1) proposes a command. Before a
person approves it, this answers *what would it actually touch?* by reading
the Living Model -- which instances run on the host, which networks and
routers its agents serve, how much compute capacity is left if it is taken out
of scheduling, which containers would be restarted -- instead of reasoning
about the command in isolation.

**It is a simulation, not an execution**: it reads the graph (one read-only
Neo4j session, `graph_db.fetch_impact_context`) and applies a small, explicit
rule set per kind of action. Nothing is run, nothing is written.

**Deterministic and honest**, like the rest of the remediation path (adr-0011):
no LLM, no randomness, so the same command against the same graph gives the
same result; and every limit is stated rather than papered over --

- `status="unmodelled"`: the command is one this module has no rule for. It
  says so and computes no impact, instead of inventing a reassuring "safe".
- `status="unavailable"`: the graph couldn't be read (Neo4j down, host not in
  the graph, host name still a `<placeholder>`). The proposal is still shown,
  clearly marked as un-simulated.
- `assumptions`: what the model could not see (e.g. "the database is assumed
  to be single-node"; "guests are assumed to live in the nova_libvirt
  container, as in Kolla"). They travel with the result.

A simulation can only ever *raise* the risk the command text alone suggested
(`effective_risk = max(static, simulated)`): a restart rated "medium" by its
verb becomes "high" once the graph shows it takes every guest on the host down.

Boilerplate text here is digit-free by design: every number in a rendered
simulation comes from `counts`/`effects` (and therefore from `raw_data`), so the
critic's numeric-grounding check (nodes/critic.py) can verify all of them.
"""
import logging
import re
from typing import Any, Optional

from .. import graph_db
from ..agents.resilience import get_breaker

logger = logging.getLogger(__name__)

SAFE, CAUTION, DISRUPTIVE = "safe", "caution", "disruptive"
_VERDICT_RANK = {SAFE: 0, CAUTION: 1, DISRUPTIVE: 2}
_VERDICT_RISK = {SAFE: "low", CAUTION: "medium", DISRUPTIVE: "high"}
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}

# How many individual effects to list; the rest are summarised by `counts`.
MAX_EFFECTS = 6

_PLACEHOLDER_RE = re.compile(r"<[A-Za-z_][\w\-]*>")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


# --------------------------------------------------------------------
# 1. Command -> Action
# --------------------------------------------------------------------

_DOCKER_RE = re.compile(r"^\s*(?:sudo\s+)?docker\s+(restart|stop|kill|start)\s+(?:-\S+\s+)*([A-Za-z0-9][\w.-]*)")
_SYSTEMCTL_RE = re.compile(r"^\s*(?:sudo\s+)?systemctl\s+(restart|stop|start)\s+([\w@.-]+)")
_SERVER_RE = re.compile(
    r"\bopenstack\s+server\s+(reboot|rebuild|delete|reset-state|migrate|evacuate|stop|start|shelve|resize)\b"
)
_BARE_REBOOT_RE = re.compile(r"(?:^|[\s;&|(])(?:sudo\s+)?(?:reboot|shutdown|poweroff|halt)\b")


def parse_action(command: str, default_host: Optional[str]) -> dict:
    """Classify a catalog command into an action this module can model.

    Returns `{"kind", "verb", "host", "target", "text"}`; `kind="unknown"`
    when no rule matches (-> `status="unmodelled"`). `host` is the node the
    action lands on: the host named in the command itself for API calls
    (`compute service set --disable compute-02 ...`), else the proposal's
    host for on-host commands."""
    text = command.strip()
    base = {"verb": None, "host": default_host, "target": None, "text": text}

    if "compute service set" in text:
        tokens = text.split()
        host = tokens[-2] if len(tokens) >= 2 else default_host
        verb = "disable" if re.search(r"--disable\b", text) else "enable" if re.search(r"--enable\b", text) else None
        return {**base, "kind": "compute-service", "verb": verb, "host": host, "target": tokens[-1]}

    if match := _DOCKER_RE.match(text):
        return {**base, "kind": "container", "verb": match.group(1), "target": match.group(2)}
    if re.match(r"^\s*(?:sudo\s+)?docker\s+system\s+prune\b", text) or re.match(r"^\s*(?:sudo\s+)?docker\s+(?:image|container|volume)\s+prune\b", text):
        return {**base, "kind": "docker-prune", "verb": "prune"}
    if match := _SYSTEMCTL_RE.match(text):
        return {**base, "kind": "host-unit", "verb": match.group(1), "target": match.group(2)}

    if match := _SERVER_RE.search(text):
        ident = _UUID_RE.search(text) or _PLACEHOLDER_RE.search(text)
        target = ident.group(0) if ident else None
        if target is None:
            # An instance given by name or short id: the first non-flag word after the verb.
            rest = [t for t in text[match.end():].split() if not t.startswith("-")]
            target = rest[0] if rest else None
        return {**base, "kind": "instance", "verb": match.group(1), "target": target}

    if _BARE_REBOOT_RE.search(text):
        return {**base, "kind": "host-reboot", "verb": "reboot"}
    if re.match(r"^\s*journalctl\b.*--vacuum", text):
        return {**base, "kind": "housekeeping", "verb": "vacuum"}
    if re.match(r"^\s*(?:sudo\s+)?(?:kill|pkill)\b", text):
        return {**base, "kind": "process-kill", "verb": "kill"}
    if re.match(r"^\s*openstack\s+(?:port\s+set|image\s+save|[\w-]+\s+show)\b", text):
        return {**base, "kind": "no-topology-effect", "verb": "none"}
    return {**base, "kind": "unknown"}


# --------------------------------------------------------------------
# 2. Container roles (Kolla): what does restarting/stopping each one touch?
# --------------------------------------------------------------------

_CONTROL_PLANE_CONTAINERS = {
    "rabbitmq": "message bus",
    "mariadb": "database",
    "keystone": "identity service",
    "haproxy": "API load balancer",
    "memcached": "token cache",
    "nova_scheduler": "scheduler",
    "nova_conductor": "conductor",
    "nova_api": "compute API",
    "neutron_server": "networking API",
    "cinder_api": "volume API",
    "glance_api": "image API",
}

# Assumption text shown with the result; kept as constants so tests and the UI
# can rely on the wording.
ASSUME_GUESTS_IN_LIBVIRT = (
    "Guests are assumed to run inside the nova_libvirt container, as in a standard Kolla deployment."
)
ASSUME_SINGLE_CONTROL_PLANE = (
    "The control plane is assumed to be a single instance; a clustered database or message bus "
    "would degrade instead of going briefly dark."
)
ASSUME_FIXED_PERSISTENT = (
    "The Living Model sees OpenStack objects and container state, not what is running inside a guest."
)


def _effect(entity: str, ident: str, name: Optional[str], effect: str, detail: str = "") -> dict:
    return {"entity": entity, "id": ident, "name": name or ident, "effect": effect, "detail": detail}


def _cap(text: str) -> str:
    # str.capitalize() would lowercase the rest, mangling instance names.
    return text[:1].upper() + text[1:]


def _names(items: list[dict], limit: int = 3) -> str:
    """First few names. "and others" carries no number on purpose: a count of
    the remainder would not be in `counts`, so the critic could not verify it."""
    shown = [i.get("name") or i.get("id") for i in items[:limit]]
    return ", ".join(shown) + (" and others" if len(items) > len(shown) else "")


def _result(verdict: str, headline: str, effects: list[dict], counts: dict, assumptions: list[str],
            reversible: bool, duration: str) -> dict:
    return {
        "verdict": verdict, "headline": headline, "effects": effects, "counts": counts,
        "assumptions": assumptions, "reversible": reversible, "duration": duration,
    }


# --------------------------------------------------------------------
# 3. Rules: (action, graph context) -> result
# --------------------------------------------------------------------

def _simulate_container(action: dict, ctx: dict) -> dict:
    name, host, verb = action["target"], ctx["node"].get("id") or action["host"], action["verb"]
    stopping = verb in ("stop", "kill")
    action_word = "stopped" if stopping else "restarted"
    gerund = "Stopping" if stopping else "Restarting"
    duration = "until it is started again" if stopping else "briefly, typically well under a minute"
    instances = ctx["instances"]
    containers = {c["name"]: c for c in ctx["containers"]}
    own = containers.get(name)
    effects = [_effect("container", f"{name}@{host}", name, action_word, f"on {host}")]
    counts: dict[str, int] = {"containers_affected": 1}
    reversible = True

    if name == "nova_libvirt":
        # In Kolla the QEMU processes live inside this container.
        effects += [_effect("instance", i["id"], i["name"], "interrupted", "guest process stops with the container")
                    for i in instances]
        counts["instances_on_host"] = len(instances)
        counts["instances_interrupted"] = len(instances)
        verdict = DISRUPTIVE if instances else CAUTION
        headline = (
            f"{gerund} nova_libvirt on {host} stops every guest running there "
            f"({len(instances)} instance(s): {_names(instances)})."
            if instances else
            f"{gerund} nova_libvirt on {host}; no instances are recorded there, so no guest is lost."
        )
        return _result(verdict, headline, effects, counts, [ASSUME_GUESTS_IN_LIBVIRT], False if instances else True, duration)

    if name == "nova_compute":
        effects.append(_effect("service", f"nova-compute@{host}", "nova-compute", "unavailable",
                               "cannot start, stop or migrate guests while it is down"))
        counts["instances_on_host"] = len(instances)
        counts["instances_unmanageable"] = len(instances)
        headline = (
            f"nova-compute on {host} is unavailable {duration}; its {len(instances)} instance(s) keep running "
            f"but cannot be managed meanwhile, and new instances are not scheduled there."
            if instances else
            f"nova-compute on {host} is unavailable {duration}; no instances are recorded there."
        )
        return _result(CAUTION, headline, effects, counts, [ASSUME_GUESTS_IN_LIBVIRT], reversible, duration)

    if name in ("neutron_l3_agent", "neutron_dhcp_agent", "neutron_openvswitch_agent", "neutron_metadata_agent"):
        served = [t for svc in ctx["services"] if svc["binary"] == name.replace("_", "-") for t in svc["serves"]]
        networks = [t for t in served if t["label"] == "Network"]
        routers = [t for t in served if t["label"] == "Router"]
        counts["networks_served"] = len(networks)
        counts["routers_served"] = len(routers)
        floating = sum(ctx["router_floating_ips"].get(r["id"], 0) for r in routers)
        counts["floating_ips_behind"] = floating
        effects += [_effect("router", r["id"], r.get("name"), "interrupted", "routing briefly handled by no agent") for r in routers]
        effects += [_effect("network", n["id"], n.get("name"), "interrupted", "agent unavailable") for n in networks]
        affected_guests = {i["id"]: i for n in networks for i in ctx["network_instances"].get(n["id"], [])}
        if name == "neutron_openvswitch_agent":
            affected_guests = {i["id"]: i for i in instances}
        counts["instances_on_affected_networks"] = len(affected_guests)
        busy = bool(routers or networks or affected_guests)
        headline = (
            f"{name} on {host} is {action_word}: it serves {len(routers)} router(s) with {floating} floating IP(s) "
            f"and {len(networks)} network(s) reaching {len(affected_guests)} instance(s); expect brief packet loss "
            f"or stale DHCP leases, not lost guests."
            if busy else
            f"{name} on {host} is {action_word}; the graph shows nothing it currently serves."
        )
        return _result(CAUTION if busy else SAFE, headline, effects, counts, [ASSUME_FIXED_PERSISTENT], reversible, duration)

    if name == "cinder_volume":
        svc = own and own.get("service_id")
        effects.append(_effect("service", svc or f"cinder-volume@{host}", "cinder-volume", "unavailable",
                               "volume attach, detach and create pause; attached volumes keep working"))
        headline = f"cinder-volume on {host} pauses volume management {duration}; volumes already attached keep working."
        return _result(CAUTION, headline, effects, counts, [ASSUME_FIXED_PERSISTENT], reversible, duration)

    if name in _CONTROL_PLANE_CONTAINERS:
        role = _CONTROL_PLANE_CONTAINERS[name]
        cp = ctx["control_plane"]
        counts["services_depending"] = cp["total"]
        counts["services_up"] = cp["up"]
        effects.append(_effect("control-plane", name, role, "unavailable",
                               "API calls and service heartbeats fail while it is down"))
        headline = (
            f"The {role} ({name}) on {host} is {action_word}: the control plane is unavailable {duration}, "
            f"and all {cp['total']} known OpenStack service(s) lose it. Running instances and their networks are not touched."
        )
        return _result(CAUTION, headline, effects, counts, [ASSUME_SINGLE_CONTROL_PLANE], reversible, duration)

    # A container Cortex knows of but has no rule for: say so, don't guess.
    headline = f"{name} on {host} is {action_word}; Cortex has no impact model for this container."
    out = _result(CAUTION, headline, effects, counts, [], reversible, duration)
    out["status"] = "unmodelled"
    return out


def _simulate_compute_service(action: dict, ctx: dict) -> dict:
    host, verb = ctx["node"].get("id") or action["host"], action["verb"]
    instances = ctx["instances"]
    fleet = ctx["compute_fleet"]
    others_up = [s for s in fleet if s["node_id"] != host and s["state"] == "up" and s["status"] != "disabled"]
    counts = {
        "instances_on_host": len(instances),
        "other_compute_hosts_available": len(others_up),
        "compute_hosts_total": len(fleet),
    }
    if verb == "enable":
        return _result(
            SAFE, f"{host} accepts new instances again; nothing running is affected.",
            [_effect("service", f"nova-compute@{host}", "nova-compute", "schedulable")], counts, [], True,
            "takes effect immediately",
        )
    effects = [_effect("service", f"nova-compute@{host}", "nova-compute", "unschedulable",
                       "no new instances are placed here; existing ones keep running")]
    effects += [_effect("instance", i["id"], i["name"], "unaffected", "keeps running") for i in instances]
    if not others_up:
        headline = (
            f"Disabling nova-compute on {host} leaves no other compute host available "
            f"({len(fleet)} known, none up and enabled): new instances could not be scheduled anywhere. "
            f"Its {len(instances)} running instance(s) are not touched."
        )
        return _result(DISRUPTIVE, headline, effects, counts, [], True, "until re-enabled")
    headline = (
        f"{host} stops receiving new instances; its {len(instances)} running instance(s) are not touched. "
        f"{len(others_up)} other compute host(s) remain available to schedule onto."
    )
    return _result(CAUTION, headline, effects, counts, [], True, "until re-enabled")


def _simulate_instance(action: dict, ctx: dict) -> dict:
    verb, target = action["verb"], action["target"]
    known = next((i for i in ctx["instances"] if i["id"] == target), None) if target else None
    real_id = target if target and not _PLACEHOLDER_RE.fullmatch(target) else None
    named = (known or {}).get("name") or real_id
    which = f"instance {named}" if named else "the instance you substitute"
    effects = [_effect("instance", target or "?", (known or {}).get("name"), "interrupted")]
    counts = {"instances_interrupted": 1, "instances_on_host": len(ctx["instances"])}
    assumptions = [] if known else ["Only one guest is touched; which one depends on the instance you fill in."]
    if verb in ("rebuild", "delete"):
        word = "reimaged (its root disk is replaced)" if verb == "rebuild" else "permanently deleted"
        return _result(DISRUPTIVE, f"{_cap(which)} would be {word}. This cannot be undone.", effects, counts,
                       assumptions, False, "immediately")
    if verb == "reset-state":
        return _result(
            CAUTION,
            f"Only the recorded state of {which} changes; the guest itself is not touched, so the record "
            f"can disagree with reality if the underlying fault remains.",
            [_effect("instance", target or "?", (known or {}).get("name"), "state-reset")],
            {"instances_state_changed": 1}, assumptions, True, "immediately",
        )
    if verb in ("migrate", "evacuate", "resize"):
        return _result(
            CAUTION,
            f"{_cap(which)} moves to another compute host; expect a short pause and a changed host in the Living Model.",
            effects, counts, assumptions, True, "briefly, typically well under a minute",
        )
    tail = "while it restarts" if verb == "reboot" else "until it is started again"
    return _result(CAUTION, f"{_cap(which)} is interrupted {tail}; no other guest is touched.", effects, counts,
                   assumptions, True, "briefly, typically well under a minute")


def _simulate_host_reboot(action: dict, ctx: dict) -> dict:
    host = ctx["node"].get("id") or action["host"]
    instances, services, containers = ctx["instances"], ctx["services"], ctx["containers"]
    effects = [_effect("node", host, host, "rebooted")]
    effects += [_effect("instance", i["id"], i["name"], "interrupted", "guest stops with the host") for i in instances]
    effects += [_effect("service", s["id"], s["binary"], "unavailable", "down until the host is back") for s in services]
    counts = {"instances_interrupted": len(instances), "services_unavailable": len(services),
              "containers_restarted": len(containers)}
    headline = (
        f"Rebooting {host} takes down {len(instances)} instance(s), {len(services)} OpenStack service(s) and "
        f"{len(containers)} container(s) until it is back."
    )
    return _result(DISRUPTIVE, headline, effects, counts, [], False, "until the host is back")


def _simulate_prune(action: dict, ctx: dict) -> dict:
    host = ctx["node"].get("id") or action["host"]
    stopped = [c for c in ctx["containers"] if c["state"] in ("down", "missing", "restarting")]
    effects = [_effect("container", f"{c['name']}@{host}", c["name"], "lost",
                       "stopped container, its writable layer is deleted") for c in stopped]
    counts = {"stopped_containers_deleted": len(stopped)}
    if stopped:
        headline = (
            f"Pruning on {host} deletes {len(stopped)} stopped container(s) ({_names([{'name': c['name']} for c in stopped])}) "
            f"along with unused images and volumes; a stopped container can no longer simply be started again."
        )
    else:
        headline = f"Pruning on {host} deletes unused images and volumes; no stopped container is recorded there."
    return _result(DISRUPTIVE, headline, effects, counts, ["Volumes not attached to a container are assumed unused."], False, "immediately")


def _simulate_host_unit(action: dict, ctx: dict) -> dict:
    host, unit = ctx["node"].get("id") or action["host"], action["target"]
    if unit == "node_exporter":
        return _result(
            CAUTION,
            f"Restarting node_exporter on {host} blinds Cortex to that host's metrics for a moment; "
            f"it may briefly show {host} as down. Nothing on the host is affected.",
            [_effect("node", host, host, "monitoring-gap", "host metrics unavailable briefly")], {}, [], True,
            "briefly, typically well under a minute",
        )
    out = _result(CAUTION, f"{unit} on {host} is {action['verb']}ed; Cortex has no impact model for this unit.",
                  [_effect("host-unit", unit or "?", unit, action["verb"] + "ed")], {}, [], True, "briefly")
    out["status"] = "unmodelled"
    return out


def _simulate_trivial(headline: str, verdict: str = SAFE, reversible: bool = True):
    def run(action: dict, ctx: dict) -> dict:
        return _result(verdict, headline.format(host=ctx["node"].get("id") or action["host"]), [], {}, [], reversible, "immediately")
    return run


_RULES = {
    "container": _simulate_container,
    "compute-service": _simulate_compute_service,
    "instance": _simulate_instance,
    "host-reboot": _simulate_host_reboot,
    "docker-prune": _simulate_prune,
    "host-unit": _simulate_host_unit,
    "housekeeping": _simulate_trivial(
        "Trimming logs on {host} frees space and touches no OpenStack object; old log lines are gone for good.",
        SAFE, False),
    "no-topology-effect": _simulate_trivial("This command changes nothing in the topology."),
}


# --------------------------------------------------------------------
# 4. Putting it together
# --------------------------------------------------------------------

def simulate(action: dict, ctx: dict) -> dict:
    """Pure: an action plus the graph context -> a simulation record."""
    rule = _RULES.get(action["kind"])
    if rule is None:
        return _finish(action, {
            "status": "unmodelled", "verdict": CAUTION, "headline":
            "Cortex has no impact model for this command, so no impact was computed. Treat it as unsimulated.",
            "effects": [], "counts": {}, "assumptions": [], "reversible": False, "duration": "unknown",
        }, ctx)
    result = rule(action, ctx)
    result.setdefault("status", "simulated")
    return _finish(action, result, ctx)


def _finish(action: dict, result: dict, ctx: dict) -> dict:
    node_health = (ctx.get("node") or {}).get("health") or "unknown"
    warnings = []
    if action["kind"] in ("container", "host-unit", "host-reboot", "docker-prune", "housekeeping", "process-kill") \
            and node_health in ("down", "unknown"):
        warnings.append(
            f"Cortex currently sees this host as {node_health}: an on-host command may not be able to reach it."
        )
    effects = result["effects"]
    return {
        "status": result["status"],
        "verdict": result["verdict"],
        "headline": result["headline"],
        "effects": effects[:MAX_EFFECTS],
        "effects_total": len(effects),
        "counts": result["counts"],
        "assumptions": result["assumptions"],
        "warnings": warnings,
        "reversible": result["reversible"],
        "duration": result["duration"],
        "action": {k: action.get(k) for k in ("kind", "verb", "host", "target")},
        "model": {"node": ctx.get("node", {}).get("id"), "node_health": node_health},
    }


def effective_risk(static_risk: str, simulation: Optional[dict]) -> str:
    """Never lower than the command-text risk; raised when the graph shows
    more is at stake. An unsimulated step keeps its static risk."""
    if not simulation or simulation.get("status") != "simulated":
        return static_risk
    simulated = _VERDICT_RISK[simulation["verdict"]]
    return simulated if _RISK_RANK[simulated] > _RISK_RANK[static_risk] else static_risk


def _unavailable(action: dict, reason: str) -> dict:
    return {
        "status": "unavailable", "verdict": None,
        "headline": f"Impact could not be simulated: {reason}. Review the command as if it were unsimulated.",
        "effects": [], "effects_total": 0, "counts": {}, "assumptions": [], "warnings": [],
        "reversible": None, "duration": None,
        "action": {k: action.get(k) for k in ("kind", "verb", "host", "target")}, "model": {},
    }


_CONTEXT_BREAKER = "impact_simulator.context"


def _fetch_context(host: str) -> Optional[dict]:
    breaker = get_breaker(_CONTEXT_BREAKER, timeout_seconds=6.0, max_retries=0)
    result = breaker.call(graph_db.fetch_impact_context, host)
    if not result.ok:
        raise RuntimeError(result.failure["message"] if result.failure else "graph read failed")
    return result.value


def simulate_command(command: str, default_host: Optional[str], _cache: Optional[dict] = None) -> dict:
    """Parse, fetch the graph context for the action's host, simulate. Never
    raises -- an unreadable graph yields an `unavailable` record."""
    cache = _cache if _cache is not None else {}
    action = parse_action(command, default_host)
    if action["kind"] == "unknown":
        return simulate(action, {"node": {}})
    host = action["host"]
    if not host or _PLACEHOLDER_RE.fullmatch(host):
        return _unavailable(action, "the host is not known yet, so there is nothing in the graph to read")
    try:
        if host not in cache:
            cache[host] = _fetch_context(host)
    except Exception as exc:  # noqa: BLE001 -- the proposal must survive an unreadable graph
        logger.warning("impact simulator: could not read the Living Model for %s: %s", host, exc)
        return _unavailable(action, "the Living Model could not be read right now")
    ctx = cache[host]
    if ctx is None:
        return _unavailable(action, f"{host} is not in the Living Model")
    return simulate(action, ctx)


def simulate_proposal(proposal: dict) -> dict:
    """The proposal with a `simulation` on its primary step and on every
    alternative, an `effective_risk` on each, `proposal["simulation"]` as the
    primary's convenience copy, and `safer_alternative` when a runnable
    alternative simulates strictly gentler than the recommended step."""
    host = proposal.get("host")
    cache: dict = {}

    def annotate(step: dict) -> dict:
        sim = simulate_command(step["command"], host, cache)
        return {**step, "simulation": sim, "effective_risk": effective_risk(step["risk"], sim)}

    primary = annotate(proposal["primary"])
    alternatives = [annotate(a) for a in proposal["alternatives"]]

    safer = None
    if primary["simulation"]["status"] == "simulated":
        rank = _VERDICT_RANK[primary["simulation"]["verdict"]]
        candidates = [
            a for a in alternatives
            if a["simulation"]["status"] == "simulated"
            and _VERDICT_RANK[a["simulation"]["verdict"]] < rank
            and not any(p not in {"<host>", "<hostname>", "<hypervisor_hostname>"} for p in a["placeholders"])
        ]
        if candidates:
            best = min(candidates, key=lambda a: _VERDICT_RANK[a["simulation"]["verdict"]])
            safer = {"command": best["command"], "description": best["description"],
                     "verdict": best["simulation"]["verdict"]}

    return {**proposal, "primary": primary, "alternatives": alternatives,
            "simulation": primary["simulation"], "safer_alternative": safer}
