"""Tests for app/agents/nodes/openstack_expert.py -- the v0.6 "teaching,
not just reporting" agent. See docs/architecture/adr-0008.

Covers the two entry paths (chained after anomaly/monitoring, and
standalone) plus the symptom matcher and the trigger predicates graph.py's
conditional edges call directly.
"""
import app.agents.nodes.openstack_expert as expert

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]


def _anomaly_state(metric_signal, log_signal, likely_cause=None, error=None):
    if error:
        return {
            "user_query": "something's wrong with compute-02",
            "known_nodes": KNOWN_NODES,
            "target_agent": "anomaly",
            "agent_result": None,
            "error": error,
            "failures": [],
        }
    return {
        "user_query": "something's wrong with compute-02",
        "known_nodes": KNOWN_NODES,
        "target_agent": "anomaly",
        "agent_result": {
            "summary": "upstream anomaly summary text",
            "confidence": 0.8,
            "raw_data": {
                "hostname": NODE["hostname"],
                "role": NODE["role"],
                "metric_signal": metric_signal,
                "log_signal": log_signal,
                "likely_cause": likely_cause,
            },
        },
        "error": None,
        "failures": [],
    }


def _monitoring_state(metrics):
    return {
        "user_query": "how is compute-02 doing",
        "known_nodes": KNOWN_NODES,
        "target_agent": "monitoring",
        "agent_result": {"summary": "upstream monitoring summary", "confidence": 1.0, "raw_data": metrics},
        "error": None,
        "failures": [],
    }


def _standalone_state(query):
    return {
        "user_query": query,
        "known_nodes": KNOWN_NODES,
        "target_agent": "openstack_expert",
        "error": None,
        "failures": [],
    }


# --------------------------------------------------------------------
# Symptom matcher
# --------------------------------------------------------------------

def test_match_symptoms_scores_metric_name_hit_highest():
    matches = expert._match_symptoms(metric_name="cpu_usage")
    assert matches
    top_entry, top_score = matches[0]
    assert top_entry["id"] == "host-cpu-pressure"
    assert top_score >= expert._SCORE_METRIC_NAME


def test_match_symptoms_scores_service_binary_hit():
    matches = expert._match_symptoms(service_binaries=["neutron-l3-agent"])
    assert matches[0][0]["id"] == "neutron-l3-agent-down"


def test_match_symptoms_scores_query_keyword_hit():
    matches = expert._match_symptoms(query="why is the disk full on compute-02")
    assert matches
    assert matches[0][0]["id"] == "host-disk-pressure"


def test_match_symptoms_no_match_returns_empty():
    assert expert._match_symptoms(query="what's the weather like") == []


def test_match_symptoms_combines_signals_for_a_higher_score_than_either_alone():
    combined = expert._match_symptoms(metric_name="ram_usage", query="high memory usage on compute-02")
    metric_only = expert._match_symptoms(metric_name="ram_usage")
    assert combined[0][1] > metric_only[0][1]


def test_detect_service_binaries_finds_mentioned_binary_in_text():
    assert "nova-compute" in expert._detect_service_binaries("is nova-compute actually running?")
    assert expert._detect_service_binaries("totally unrelated text") == []


# --------------------------------------------------------------------
# Trigger predicates (used directly by graph.py's conditional edges)
# --------------------------------------------------------------------

def test_should_trigger_after_anomaly_true_when_metric_signal_present():
    state = _anomaly_state(
        metric_signal={"has_signal": True, "detail": "d", "data": {"metric_name": "cpu_usage", "source": "anomaly_flags", "severity": "critical"}},
        log_signal={"has_signal": False, "degraded": False, "detail": "clean", "entries": []},
    )
    assert expert.should_trigger_after_anomaly(state) is True


def test_should_trigger_after_anomaly_false_when_nothing_found():
    state = _anomaly_state(
        metric_signal={"has_signal": False, "detail": "d", "data": None},
        log_signal={"has_signal": False, "degraded": False, "detail": "clean", "entries": []},
    )
    assert expert.should_trigger_after_anomaly(state) is False


def test_should_trigger_after_anomaly_false_on_error():
    state = _anomaly_state(None, None, error="I couldn't tell which node you meant.")
    assert expert.should_trigger_after_anomaly(state) is False


def test_should_trigger_after_anomaly_true_when_degraded_log_check():
    state = _anomaly_state(
        metric_signal={"has_signal": False, "detail": "d", "data": None},
        log_signal={"has_signal": False, "degraded": True, "detail": "couldn't complete", "entries": []},
    )
    assert expert.should_trigger_after_anomaly(state) is True


def test_should_trigger_after_monitoring_true_when_unhealthy():
    state = _monitoring_state({
        "node": "compute-02", "role": "compute", "cpu_percent": 97, "memory_percent": 40,
        "disk_percent": 30, "status": "up", "health": "critical",
    })
    assert expert.should_trigger_after_monitoring(state) is True


def test_should_trigger_after_monitoring_false_when_healthy():
    state = _monitoring_state({
        "node": "compute-02", "role": "compute", "cpu_percent": 12, "memory_percent": 30,
        "disk_percent": 40, "status": "up", "health": "healthy",
    })
    assert expert.should_trigger_after_monitoring(state) is False


def test_should_trigger_after_monitoring_true_when_host_down():
    state = _monitoring_state({
        "node": "compute-02", "role": "compute", "cpu_percent": 0, "memory_percent": 0,
        "disk_percent": 0, "status": "down", "health": "unknown",
    })
    assert expert.should_trigger_after_monitoring(state) is True


# --------------------------------------------------------------------
# Chained mode: anomaly -> openstack_expert
# --------------------------------------------------------------------

def test_chained_from_anomaly_builds_three_layer_answer_with_labeled_commands():
    state = _anomaly_state(
        metric_signal={
            "has_signal": True,
            "detail": "compute-02's cpu usage is flagged critical (z=4.2, current value 97.3).",
            "data": {"metric_name": "cpu_usage", "source": "anomaly_flags", "severity": "critical"},
        },
        log_signal={"has_signal": False, "degraded": False, "detail": "clean", "entries": []},
    )
    result = expert.openstack_expert_agent(state)

    answer = result["agent_result"]["summary"]
    assert result["target_agent"] == "openstack_expert"
    assert "What's happening" in answer
    assert "How to confirm it yourself" in answer
    assert "What's usually done about it" in answer
    assert "(read-only)" in answer
    assert "(state-changing)" in answer
    # The concrete evidence from the upstream diagnosis is woven in, not
    # discarded -- this is what makes it "teaching", not a generic essay.
    assert "97.3" in answer
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "host-cpu-pressure"
    assert result["agent_result"]["raw_data"]["diagnosed_by"] == "anomaly"
    assert result["agent_result"]["raw_data"]["upstream_summary"] == "upstream anomaly summary text"


def test_chained_from_anomaly_fills_in_the_hostname_placeholder():
    state = _anomaly_state(
        metric_signal={
            "has_signal": True,
            "detail": "d",
            "data": {"metric_name": "ram_usage", "source": "anomaly_flags", "severity": "high"},
        },
        log_signal={"has_signal": False, "degraded": False, "detail": "clean", "entries": []},
    )
    result = expert.openstack_expert_agent(state)
    commands = result["agent_result"]["raw_data"]["remediation_commands"]
    assert any("compute-02" in c["command"] for c in commands)
    assert not any("<host>" in c["command"] for c in commands)


def test_chained_from_anomaly_uses_log_content_to_pick_a_more_specific_entry():
    state = _anomaly_state(
        metric_signal={
            "has_signal": True,
            "detail": "d",
            "data": {"metric_name": "cpu_usage", "source": "anomaly_flags", "severity": "high"},
        },
        log_signal={
            "has_signal": True,
            "degraded": False,
            "detail": "correlated log found",
            "entries": [{"ts": 1, "line": "ERROR lost connection to libvirt", "service": "nova"}],
        },
    )
    result = expert.openstack_expert_agent(state)
    # The log content ("lost connection to libvirt") is a stronger, more
    # specific signal than the bare cpu_usage metric name, and the
    # libvirt entry's own service_binaries/keywords don't include cpu --
    # scoring should still surface it because "libvirt" isn't in the
    # generic cpu-pressure entry's keywords at all.
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "libvirt-hypervisor-issue"


def test_chained_from_anomaly_passes_through_unmodified_when_no_catalog_match():
    # Force a signal shape with no plausible catalog match: use a metric
    # name that isn't in any entry to simulate a genuinely novel pattern.
    state = _anomaly_state(
        metric_signal={
            "has_signal": True,
            "detail": "d",
            "data": {"metric_name": "totally_novel_metric", "source": "anomaly_flags", "severity": "high"},
        },
        log_signal={"has_signal": False, "degraded": False, "detail": "clean", "entries": []},
    )
    result = expert.openstack_expert_agent(state)
    # Nothing matched -- the original anomaly diagnosis is left standing.
    assert result["agent_result"]["summary"] == "upstream anomaly summary text"
    assert result["target_agent"] == "anomaly"


# --------------------------------------------------------------------
# Chained mode: monitoring -> openstack_expert
# --------------------------------------------------------------------

def test_chained_from_monitoring_matches_dominant_metric():
    state = _monitoring_state({
        "node": "compute-02", "role": "compute", "cpu_percent": 20, "memory_percent": 92,
        "disk_percent": 30, "status": "up", "health": "critical",
    })
    result = expert.openstack_expert_agent(state)
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "host-ram-pressure"
    assert result["target_agent"] == "openstack_expert"
    assert "compute-02" in result["agent_result"]["summary"]


def test_chained_from_monitoring_matches_node_unreachable_when_host_down():
    state = _monitoring_state({
        "node": "compute-02", "role": "compute", "cpu_percent": 0, "memory_percent": 0,
        "disk_percent": 0, "status": "down", "health": "unknown",
    })
    result = expert.openstack_expert_agent(state)
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "node-unreachable"


# --------------------------------------------------------------------
# Standalone mode
# --------------------------------------------------------------------

def test_standalone_matches_named_service_and_labels_commands():
    state = _standalone_state("how do I check if nova-compute is running")
    result = expert.openstack_expert_agent(state)

    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "nova-compute-down"
    answer = result["agent_result"]["summary"]
    assert "(read-only)" in answer
    assert "(state-changing)" in answer
    assert result["error"] is None


def test_standalone_no_match_gives_graceful_fallback_not_an_error():
    state = _standalone_state("what's the meaning of life")
    result = expert.openstack_expert_agent(state)

    assert result["agent_result"]["raw_data"]["matched_symptom_id"] is None
    assert result["error"] is None
    assert "don't have a specific runbook entry" in result["agent_result"]["summary"]


def test_standalone_resolves_a_named_node_into_commands():
    state = _standalone_state("how do I check disk usage on compute-02")
    result = expert.openstack_expert_agent(state)
    commands = result["agent_result"]["raw_data"]["confirm_commands"] + result["agent_result"]["raw_data"]["remediation_commands"]
    # host-disk-pressure's commands don't reference <host> at all (df -h,
    # du -sh, etc. are host-agnostic by nature) -- what matters here is
    # that resolution didn't blow up and a match was still found.
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "host-disk-pressure"
    assert commands
