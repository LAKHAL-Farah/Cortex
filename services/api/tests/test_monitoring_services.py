"""monitoring_agent's v1.4 services-scope widening (see nodes/monitoring.py's
own module docstring): "which OpenStack services are up/down", fleet-wide,
scoped to one node, or scoped to one named binary. No LLM or Neo4j -- both
faked, same convention test_multi_node.py already uses for collect_metrics.
"""
import pytest

from app.agents.nodes import monitoring, openstack_expert


def _n(host, role, ip):
    return {"hostname": host, "role": role, "instance": f"{ip}:9100"}


C1 = _n("compute1-sim", "compute", "10.0.0.21")
C2 = _n("compute2-sim", "compute", "10.0.0.22")
KNOWN = [C1, C2]


def _svc(binary, node, state, **extra):
    return {"binary": binary, "node_id": node, "host": node, "zone": "nova", "state": state, **extra}


@pytest.fixture
def no_llm(monkeypatch):
    def _raise(**kwargs):
        raise monitoring.LLMConfigError("no key")

    monkeypatch.setattr(monitoring, "get_chat_model", _raise)


def _ask(monkeypatch, query, services, known=KNOWN):
    monkeypatch.setattr(monitoring.graph_db, "fetch_services", lambda: services)
    # collect_metrics should never be called on the services path -- if it
    # is, something misrouted a services question into the metrics path.
    monkeypatch.setattr(monitoring, "collect_metrics", lambda: (_ for _ in ()).throw(AssertionError("collect_metrics called for a services question")))
    return monitoring.monitoring_agent({"user_query": query, "known_nodes": known, "resolved_entities": {}})


# ------------------------------------------------------------- detection --

@pytest.mark.parametrize(
    "query, expected_hit, expected_binaries",
    [
        ("which openstack services are down", True, []),
        ("are all services healthy", True, []),
        ("is nova-compute running on compute1-sim", True, ["nova-compute"]),
        ("is neutron-l3-agent up", True, ["neutron-l3-agent"]),
        ("what is the CPU on compute1-sim", False, []),
        ("compare compute1-sim and compute2-sim", False, []),
        ("is the service desk open", True, []),  # bare keyword still counts, agent will just find nothing
    ],
)
def test_mentions_services(query, expected_hit, expected_binaries):
    hit, binaries = monitoring._mentions_services(query)
    assert hit is expected_hit
    assert binaries == expected_binaries


# ------------------------------------------------------------- fleet-wide --

def test_services_fleet_wide_reports_everything(monkeypatch, no_llm):
    services = [
        _svc("nova-compute", "compute1-sim", "up"),
        _svc("nova-compute", "compute2-sim", "down"),
        _svc("neutron-l3-agent", "compute1-sim", "unreachable"),
    ]
    state = _ask(monkeypatch, "which openstack services are down", services)
    assert state["error"] is None
    raw = state["agent_result"]["raw_data"]
    assert raw["scope"] == "services"
    assert raw["counts"] == {"total": 3, "up": 1, "down": 1, "unreachable": 1, "unknown": 0}
    # down-first ordering
    assert [r["state"] for r in raw["services"]] == ["down", "unreachable", "up"]
    assert "compute2-sim" in state["agent_result"]["summary"]


def test_services_all_healthy_still_answers(monkeypatch, no_llm):
    services = [_svc("nova-compute", h["hostname"], "up") for h in KNOWN]
    state = _ask(monkeypatch, "are any services down", services)
    assert state["error"] is None
    assert state["agent_result"]["raw_data"]["counts"]["down"] == 0
    assert "Nothing reporting down or unreachable" in "".join(state["agent_result"]["raw_data"]["insights"])


# -------------------------------------------------------------- scoping --

def test_services_scoped_to_one_named_node(monkeypatch, no_llm):
    services = [
        _svc("nova-compute", "compute1-sim", "up"),
        _svc("nova-compute", "compute2-sim", "down"),
    ]
    state = _ask(monkeypatch, "are the services on compute2-sim ok", services)
    rows = state["agent_result"]["raw_data"]["services"]
    assert len(rows) == 1
    assert rows[0]["node"] == "compute2-sim"
    assert state["resolved_entities"]["last_node"]["hostname"] == "compute2-sim"


def test_services_scoped_to_one_named_binary(monkeypatch, no_llm):
    services = [
        _svc("nova-compute", "compute1-sim", "up"),
        _svc("nova-scheduler", "compute1-sim", "down"),
    ]
    state = _ask(monkeypatch, "is nova-scheduler up", services)
    rows = state["agent_result"]["raw_data"]["services"]
    assert len(rows) == 1
    assert rows[0]["binary"] == "nova-scheduler"
    assert rows[0]["state"] == "down"


# ---------------------------------------------------------------- errors --

def test_services_no_match_is_a_clean_error_not_a_crash(monkeypatch, no_llm):
    state = _ask(monkeypatch, "is nova-scheduler up", [_svc("nova-compute", "compute1-sim", "up")])
    assert state["agent_result"] is None
    assert "nova-scheduler" in state["error"]


def test_services_graph_unreachable_is_a_clean_error(monkeypatch, no_llm):
    def _raise():
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(monitoring.graph_db, "fetch_services", _raise)
    state = monitoring.monitoring_agent(
        {"user_query": "which services are down", "known_nodes": KNOWN, "resolved_entities": {}}
    )
    assert state["agent_result"] is None
    assert "topology graph" in state["error"]


# --------------------------------------------------------- no auto-chain --

def test_services_answer_never_chains_into_the_expert_agent(monkeypatch, no_llm):
    """A services answer -- even one reporting real down services -- has
    no single "status"/"health" pair, so it must not fall into
    should_trigger_after_monitoring's `!= "up"` check (that would read the
    *absence* of those keys as "not up" and wrongly chain into a
    symptom-catalog walkthrough on every services question, healthy or
    not)."""
    services = [
        _svc("nova-compute", "compute1-sim", "up"),
        _svc("nova-compute", "compute2-sim", "down"),
    ]
    state = _ask(monkeypatch, "what services are running on compute2-sim", services)
    assert state["agent_result"] is not None
    assert openstack_expert.should_trigger_after_monitoring(state) is False


# ------------------------------------------------------- metrics untouched --

def test_a_plain_metrics_question_still_uses_the_old_path(monkeypatch, no_llm):
    """Sanity check that widening monitoring didn't accidentally route an
    ordinary metrics question through the new services branch."""
    monkeypatch.setattr(
        monitoring.graph_db,
        "fetch_services",
        lambda: (_ for _ in ()).throw(AssertionError("fetch_services called for a metrics question")),
    )
    monkeypatch.setattr(
        monitoring,
        "collect_metrics",
        lambda: [
            {
                "instance": C1["instance"], "node": "compute1-sim", "role": "compute",
                "cpu_percent": 42, "memory_percent": 10, "swap_percent": 0,
                "disk_percent": 10, "load1": 1.0, "uptime": "1d", "status": "ok", "health": "healthy",
            }
        ],
    )
    state = monitoring.monitoring_agent(
        {"user_query": "what is the CPU on compute1-sim", "known_nodes": KNOWN, "resolved_entities": {}}
    )
    assert state["error"] is None
    assert state["agent_result"]["raw_data"]["cpu_percent"] == 42
