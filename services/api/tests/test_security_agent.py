"""Tests for app/agents/nodes/security.py -- the Security Agent (v0.9,
Phase 5), sub-orchestrating four near-independent checks (auth-anomaly,
sec-group-diff, CVE-match, eBPF-signal) into one AgentResult.

Same style as test_network_agent.py/test_anomaly_agent.py: every external
call (loki_client.query_range, security_audit.get_node_security_groups,
cve_feed.get_package_inventory, ebpf_signal.get_node_ebpf_alerts) is
monkeypatched at its call site, and no NVIDIA_API_KEY is set so narration
always takes the deterministic LLMConfigError fallback path.
"""
from app.agents.nodes import security
from app.services import cve_feed, ebpf_signal, security_audit

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]


def _clean(monkeypatch):
    monkeypatch.setattr(security.loki_client, "query_range", lambda *a, **k: [])
    monkeypatch.setattr(
        security_audit, "get_node_security_groups",
        lambda hostname, conn=None: {"hostname": hostname, "security_groups": [], "risky_rules": []},
    )
    monkeypatch.setattr(cve_feed, "get_package_inventory", lambda hostname: [])
    monkeypatch.setattr(ebpf_signal, "get_node_ebpf_alerts", lambda hostname: [])


def _loki_stream(lines, ts_ns=1_700_000_000_000_000_000):
    return [{"stream": {"service": "sshd"}, "values": [[str(ts_ns + i), line] for i, line in enumerate(lines)]}]


def _sec_group(risky=False):
    rule = {
        "id": "r1", "security_group_id": "sg1", "direction": "ingress", "ethertype": "IPv4",
        "protocol": "tcp", "port_range_min": 22, "port_range_max": 22, "remote_ip_prefix": "0.0.0.0/0",
    }
    groups = [{"id": "sg1", "name": "default", "rules": [rule]}]
    risky_rules = [{"security_group": "default", "rule": rule, "reason": "SSH (port 22) open to the world"}] if risky else []
    return {"hostname": NODE["hostname"], "security_groups": groups, "risky_rules": risky_rules}


def _package(name="openssh-server", version="9.3p1"):
    return {"name": name, "version": version}


def _ebpf_alert(priority="critical", rule="Terminal shell in container", output="a shell was spawned"):
    return {"rule": rule, "priority": priority, "output": output, "time": "2026-08-25T12:00:00Z"}


# --------------------------------------------------------------------
# Clean bill of health across all four checks
# --------------------------------------------------------------------

def test_security_agent_reports_clean_reading_with_high_confidence(monkeypatch):
    _clean(monkeypatch)

    state = {"user_query": "any security issues on compute-02", "known_nodes": KNOWN_NODES}
    result = security.security_agent(state)

    agent_result = result["agent_result"]
    assert result["error"] is None
    assert agent_result["confidence"] == security._NO_SIGNAL_CONFIDENCE
    assert agent_result["raw_data"]["has_signal"] is False
    assert "compute-02" in agent_result["summary"]
    assert result["resolved_entities"]["last_agent"] == "security"


# --------------------------------------------------------------------
# Each sub-check independently flags something
# --------------------------------------------------------------------

def test_auth_anomaly_alone_gets_the_lowest_signal_confidence(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setattr(
        security.loki_client, "query_range",
        lambda *a, **k: _loki_stream(["Failed password for root from 1.2.3.4 port 51000 ssh2"]),
    )

    result = security.security_agent({"user_query": "check auth on compute-02", "known_nodes": KNOWN_NODES})

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["has_signal"] is True
    assert agent_result["raw_data"]["auth_signal"]["has_signal"] is True
    # Log-only signal -- weighted below eBPF/config signals, per the
    # roadmap's explicit confidence-weighting requirement.
    assert agent_result["confidence"] == security._AUTH_LOG_ONLY_CONFIDENCE


def test_sec_group_diff_alone_gets_config_signal_confidence(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setattr(security_audit, "get_node_security_groups", lambda hostname, conn=None: _sec_group(risky=True))

    result = security.security_agent({"user_query": "audit security groups on compute-02", "known_nodes": KNOWN_NODES})

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["sec_group_signal"]["has_signal"] is True
    assert "SSH" in agent_result["raw_data"]["sec_group_signal"]["detail"]
    assert agent_result["confidence"] == security._CONFIG_SIGNAL_CONFIDENCE


def test_cve_match_alone_gets_config_signal_confidence(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setattr(cve_feed, "get_package_inventory", lambda hostname: [_package("openssh-server", "9.3p1")])

    result = security.security_agent({"user_query": "check for CVEs on compute-02", "known_nodes": KNOWN_NODES})

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["cve_signal"]["has_signal"] is True
    assert "CVE-2024-6387" in agent_result["raw_data"]["cve_signal"]["detail"]
    assert agent_result["confidence"] == security._CONFIG_SIGNAL_CONFIDENCE


def test_cve_match_ignores_patched_versions(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setattr(cve_feed, "get_package_inventory", lambda hostname: [_package("openssh-server", "9.9p1")])

    result = security.security_agent({"user_query": "check for CVEs on compute-02", "known_nodes": KNOWN_NODES})

    assert result["agent_result"]["raw_data"]["cve_signal"]["has_signal"] is False


def test_ebpf_signal_alone_gets_the_highest_confidence(monkeypatch):
    """The literal roadmap requirement: eBPF/kernel-level signal weighted
    above every other sub-check."""
    _clean(monkeypatch)
    monkeypatch.setattr(ebpf_signal, "get_node_ebpf_alerts", lambda hostname: [_ebpf_alert()])

    result = security.security_agent({"user_query": "any suspicious activity on compute-02", "known_nodes": KNOWN_NODES})

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["ebpf_signal"]["has_signal"] is True
    assert agent_result["confidence"] == security._EBPF_SIGNAL_CONFIDENCE
    assert agent_result["confidence"] > security._CONFIG_SIGNAL_CONFIDENCE
    assert agent_result["confidence"] > security._AUTH_LOG_ONLY_CONFIDENCE


def test_ebpf_signal_outranks_auth_signal_when_both_fire(monkeypatch):
    """Even when the weaker (log-only) signal also fires, the presence of
    a kernel-level signal is what should set the reported confidence --
    the ladder isn't additive, it's "trust the strongest evidence"."""
    _clean(monkeypatch)
    monkeypatch.setattr(
        security.loki_client, "query_range",
        lambda *a, **k: _loki_stream(["Failed password for root from 1.2.3.4 port 51000 ssh2"]),
    )
    monkeypatch.setattr(ebpf_signal, "get_node_ebpf_alerts", lambda hostname: [_ebpf_alert()])

    result = security.security_agent({"user_query": "is compute-02 compromised", "known_nodes": KNOWN_NODES})

    assert result["agent_result"]["confidence"] == security._EBPF_SIGNAL_CONFIDENCE


# --------------------------------------------------------------------
# Degrade behavior -- a sub-check that can't run is "unknown", not "clean"
# --------------------------------------------------------------------

def test_a_dead_ebpf_sensor_degrades_that_sub_check_without_crashing(monkeypatch):
    _clean(monkeypatch)

    def _dead(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(ebpf_signal, "get_node_ebpf_alerts", _dead)

    result = security.security_agent({"user_query": "any security issues on compute-02", "known_nodes": KNOWN_NODES})

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["ebpf_signal"]["degraded"] is True
    assert agent_result["raw_data"]["ebpf_signal"]["has_signal"] is False
    # Degraded caps confidence -- "unknown" must never read as "confirmed
    # clean" at full confidence.
    assert agent_result["confidence"] <= security._DEGRADED_CONFIDENCE_CAP
    assert len(result["failures"]) == 1
    assert result["failures"][0]["source"] == "security.ebpf"


def test_all_four_sub_checks_dead_still_returns_a_finding_not_a_crash(monkeypatch):
    def _dead(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(security.loki_client, "query_range", _dead)
    monkeypatch.setattr(security_audit, "get_node_security_groups", _dead)
    monkeypatch.setattr(cve_feed, "get_package_inventory", _dead)
    monkeypatch.setattr(ebpf_signal, "get_node_ebpf_alerts", _dead)

    result = security.security_agent({"user_query": "any security issues on compute-02", "known_nodes": KNOWN_NODES})

    assert result["error"] is None
    assert result["agent_result"] is not None
    assert result["agent_result"]["confidence"] <= security._DEGRADED_CONFIDENCE_CAP
    assert len(result["failures"]) == 4


# --------------------------------------------------------------------
# CVE version comparison (pure function, no mocking needed)
# --------------------------------------------------------------------

def test_version_lt_handles_common_debian_style_suffixes():
    assert cve_feed._version_lt("9.3p1", "9.8p1") is True
    assert cve_feed._version_lt("9.8p1", "9.8p1") is False
    assert cve_feed._version_lt("9.9p1-1ubuntu1", "9.8p1") is False
    assert cve_feed._version_lt("1.1.10", "1.1.12") is True


def test_match_cves_only_flags_known_packages_below_fixed_version():
    packages = [
        {"name": "openssh-server", "version": "9.3p1"},
        {"name": "openssh-server", "version": "9.9p1"},
        {"name": "some-unknown-package", "version": "1.0.0"},
    ]
    matches = cve_feed.match_cves(packages)
    assert len(matches) == 1
    assert matches[0]["package"] == "openssh-server"
    assert matches[0]["installed_version"] == "9.3p1"
    assert matches[0]["cve_id"] == "CVE-2024-6387"
