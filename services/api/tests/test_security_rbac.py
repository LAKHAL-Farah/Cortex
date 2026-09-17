"""Tests for routers/agents.py's v0.9 RBAC output filtering -- Security
data is sensitive in a way Monitoring/Prediction/Network's isn't (see
nodes/security.py's module docstring), so a non-admin ("viewer") account
must never receive specific CVE IDs, security-group rules, eBPF alert
output, or auth-log lines, whether Security was the direct answer, the
winning cross-agent theory, or merely a corroborating one.

Tests the pure filtering functions directly rather than driving the full
FastAPI app + DB + JWT stack -- consistent with this codebase's own
"isolate and test the logic directly" style for genuinely pure functions
(see test_cross_agent_arbitration.py for the same approach applied to
anomaly_arbitrate).
"""
from app.routers.agents import (
    _RESTRICTED_NOTICE,
    _filter_security_response_for_role,
    _filter_security_steps_for_role,
    _redact_security_raw_data,
    _security_agent_involved,
)


def _security_raw_data(has_signal=True):
    return {
        "hostname": "compute-02",
        "role": "compute",
        "has_signal": has_signal,
        "auth_signal": {"has_signal": False, "degraded": False, "detail": "clean", "entries": []},
        "sec_group_signal": {"has_signal": False, "degraded": False, "detail": "clean", "risky_rules": []},
        "cve_signal": {
            "has_signal": True, "degraded": False,
            "detail": "1 known-vulnerable package on compute-02, worst: CVE-2024-6387 (critical) in openssh-server 9.3p1 -- fixed in 9.8p1.",
            "matches": [{"cve_id": "CVE-2024-6387", "package": "openssh-server", "installed_version": "9.3p1"}],
        },
        "ebpf_signal": {"has_signal": False, "degraded": False, "detail": "clean", "alerts": []},
    }


# --------------------------------------------------------------------
# _security_agent_involved
# --------------------------------------------------------------------

def test_involved_when_security_is_the_direct_agent():
    assert _security_agent_involved("security", {}) is True


def test_involved_when_security_won_cross_agent_arbitration():
    assert _security_agent_involved("anomaly", {"investigating_agent": "security"}) is True


def test_involved_when_security_only_corroborated():
    raw_data = {"investigating_agent": "network", "cross_agent_findings": [{"agent": "security", "confidence": 0.6}]}
    assert _security_agent_involved("anomaly", raw_data) is True

    raw_data2 = {"investigating_agent": "network", "multi_node_findings": [
        {"hostname": "compute-01", "agent": "network"}, {"hostname": "compute-02", "agent": "security"},
    ]}
    assert _security_agent_involved("anomaly", raw_data2) is True


def test_not_involved_when_security_never_appears():
    raw_data = {"investigating_agent": "network", "cross_agent_findings": [{"agent": "anomaly", "confidence": 0.6}]}
    assert _security_agent_involved("network", raw_data) is False
    assert _security_agent_involved("monitoring", None) is False


# --------------------------------------------------------------------
# _redact_security_raw_data
# --------------------------------------------------------------------

def test_redact_strips_security_sub_signal_specifics_but_keeps_booleans():
    redacted = _redact_security_raw_data(_security_raw_data())
    assert redacted["hostname"] == "compute-02"  # non-sensitive, kept
    assert redacted["has_signal"] is True  # boolean summary, kept
    cve = redacted["cve_signal"]
    assert cve["has_signal"] is True
    assert cve["restricted"] is True
    assert "matches" not in cve
    assert "detail" not in cve
    assert "CVE-2024-6387" not in str(redacted)


def test_redact_only_touches_securitys_own_entries_in_cross_agent_lists():
    raw_data = {
        "investigating_agent": "network",
        "cross_agent_findings": [
            {"agent": "network", "confidence": 0.9, "summary": "down neutron agent on compute-02", "has_signal": True},
            {"agent": "security", "confidence": 0.6, "summary": "CVE-2024-6387 found in openssh-server", "has_signal": True},
        ],
    }
    redacted = _redact_security_raw_data(raw_data)
    network_entry = next(f for f in redacted["cross_agent_findings"] if f["agent"] == "network")
    security_entry = next(f for f in redacted["cross_agent_findings"] if f["agent"] == "security")
    assert network_entry["summary"] == "down neutron agent on compute-02"  # untouched
    assert "summary" not in security_entry
    assert security_entry["restricted"] is True
    assert "CVE" not in str(security_entry)


def test_redact_handles_none():
    assert _redact_security_raw_data(None) is None


# --------------------------------------------------------------------
# _filter_security_response_for_role
# --------------------------------------------------------------------

def test_admin_gets_the_full_unredacted_response():
    answer = "openssh-server 9.3p1 on compute-02 is vulnerable to CVE-2024-6387."
    raw_data = _security_raw_data()
    filtered_answer, filtered_raw = _filter_security_response_for_role(answer, raw_data, "security", "admin")
    assert filtered_answer == answer
    assert filtered_raw == raw_data


def test_viewer_gets_the_generic_notice_and_redacted_raw_data_for_a_security_answer():
    answer = "openssh-server 9.3p1 on compute-02 is vulnerable to CVE-2024-6387."
    raw_data = _security_raw_data()
    filtered_answer, filtered_raw = _filter_security_response_for_role(answer, raw_data, "security", "viewer")
    assert filtered_answer == _RESTRICTED_NOTICE
    assert "CVE-2024-6387" not in filtered_answer
    assert "CVE-2024-6387" not in str(filtered_raw)


def test_viewer_gets_untouched_response_when_security_never_contributed():
    answer = "compute-02's CPU usage is flagged critical."
    raw_data = {"hostname": "compute-02", "has_signal": True, "likely_cause": "runaway process"}
    filtered_answer, filtered_raw = _filter_security_response_for_role(answer, raw_data, "anomaly", "viewer")
    assert filtered_answer == answer
    assert filtered_raw == raw_data


def test_viewer_gets_redacted_even_when_security_only_corroborated_the_winning_theory():
    """The over-cautious-by-design case: Network won arbitration, but
    Security also fired on the same/another host, so the free-form
    cross-agent narrative *could* have named Security's specifics --
    redact the whole answer rather than risk a partial leak."""
    answer = "compute-01's Neutron agent is down; compute-02 also shows a CVE-2024-6387 exposure."
    raw_data = {
        "investigating_agent": "network",
        "multi_node_findings": [
            {"hostname": "compute-01", "agent": "network", "summary": "down neutron agent"},
            {"hostname": "compute-02", "agent": "security", "summary": "CVE-2024-6387 exposure"},
        ],
    }
    filtered_answer, filtered_raw = _filter_security_response_for_role(answer, raw_data, "anomaly", "viewer")
    assert filtered_answer == _RESTRICTED_NOTICE
    network_entry = next(f for f in filtered_raw["multi_node_findings"] if f["agent"] == "network")
    assert network_entry["summary"] == "down neutron agent"  # kept -- not Security's data


# --------------------------------------------------------------------
# _filter_security_steps_for_role
# --------------------------------------------------------------------

def _steps():
    return [
        {"node": "router", "status": "ok", "duration_ms": 5, "timestamp": "t", "detail": {"intent": "security", "target_agent": "security"}},
        {"node": "security", "status": "ok", "duration_ms": 900, "timestamp": "t", "detail": {"confidence": 0.8, "summary": "CVE-2024-6387 found on compute-02"}},
        {"node": "critic", "status": "ok", "duration_ms": 0, "timestamp": "t", "detail": {"critic_verdict": None}},
        {"node": "compose", "status": "ok", "duration_ms": 1, "timestamp": "t", "detail": {}},
    ]


def test_admin_sees_all_step_summaries_unredacted():
    filtered = _filter_security_steps_for_role(_steps(), involved=True, role="admin")
    assert filtered == _steps()


def test_viewer_gets_the_security_step_summary_redacted():
    filtered = _filter_security_steps_for_role(_steps(), involved=True, role="viewer")
    security_step = next(s for s in filtered if s["node"] == "security")
    assert security_step["detail"]["summary"] == _RESTRICTED_NOTICE
    assert "CVE" not in security_step["detail"]["summary"]
    # Every other step is untouched.
    router_step = next(s for s in filtered if s["node"] == "router")
    assert router_step["detail"]["target_agent"] == "security"


def test_viewer_steps_untouched_when_security_not_involved():
    steps = [{"node": "monitoring", "status": "ok", "duration_ms": 5, "timestamp": "t", "detail": {"summary": "CPU is fine"}}]
    filtered = _filter_security_steps_for_role(steps, involved=False, role="viewer")
    assert filtered == steps


def test_arbitrate_step_summary_also_redacted_when_security_corroborated():
    steps = [
        {"node": "anomaly", "status": "ok", "duration_ms": 1200, "timestamp": "t",
         "detail": {"confidence": 0.9, "summary": "compute-01 network issue; compute-02 shows CVE-2024-6387",
                    "contributing_agents": ["anomaly", "network", "security"]}},
    ]
    filtered = _filter_security_steps_for_role(steps, involved=True, role="viewer")
    assert filtered[0]["detail"]["summary"] == _RESTRICTED_NOTICE
