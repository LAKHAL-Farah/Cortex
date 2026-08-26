"""Tests for app/agents/nodes/anomaly.py -- the v0.4 "first incident
investigation" agent.

Unlike monitoring/prediction/rag (single data source each), this node does
internal sub-orchestration: a metric-check sub-step and a log-check
sub-step that each gather independent evidence and get merged into one
AgentResult. These tests exercise that merge directly at the node level
(same style as test_forecast_threshold.py's pure-function tests) rather
than through the full graph/HTTP endpoint, so they don't need a real
Postgres, Loki, or Prometheus -- every external call the node makes
(crud.list_open_anomaly_flags, collect_metrics, loki_client.query_range)
is monkeypatched at its call site, matching test_logs.py's convention of
faking the client module wholesale.

No NVIDIA_API_KEY is set for these tests, so _narrate always takes the
LLMConfigError fallback path -- the same deterministic-fallback contract
every other agent's tests implicitly rely on (see llm_client.py).
"""
from types import SimpleNamespace

import app.agents.nodes.anomaly as anomaly
from app.services import loki_client


NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]


def _flag(metric_name="cpu_usage", severity="critical", z_score=4.2, current_value=97.3,
          method="robust_zscore", detected_at=None):
    from datetime import datetime
    return SimpleNamespace(
        hostname=NODE["hostname"],
        metric_name=metric_name,
        severity=severity,
        z_score=z_score,
        current_value=current_value,
        method=method,
        detected_at=detected_at or datetime(2026, 8, 25, 12, 0, 0),
    )


def _loki_stream(lines, host=NODE["hostname"], service="nova"):
    return [{
        "stream": {"host": host, "job": service, "service": service},
        "values": [[str(ts_ns), line] for ts_ns, line in lines],
    }]


# --------------------------------------------------------------------
# Full node: both signals present -> merged narrative, single confidence
# --------------------------------------------------------------------

def test_anomaly_agent_merges_metric_and_log_evidence(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])
    monkeypatch.setattr(
        loki_client,
        "query_range",
        lambda *a, **k: _loki_stream([
            (1756123200000000000, "2026-08-25T12:00:00Z ERROR [nova] compute-02 lost connection to hypervisor"),
        ]),
    )

    state = {"user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_agent(state)

    agent_result = result["agent_result"]
    assert result["error"] is None
    assert agent_result is not None

    raw = agent_result["raw_data"]
    # Evidence includes both a metric signal and a correlated log entry...
    assert raw["metric_signal"]["has_signal"] is True
    assert raw["metric_signal"]["data"]["severity"] == "critical"
    assert raw["log_signal"]["has_signal"] is True
    assert "lost connection to hypervisor" in raw["log_signal"]["entries"][0]["line"]

    # ...merged into one narrative...
    assert isinstance(agent_result["summary"], str) and agent_result["summary"]
    assert "compute-02" in agent_result["summary"]

    # ...with a single confidence score.
    assert isinstance(agent_result["confidence"], float)
    assert 0.0 <= agent_result["confidence"] <= 1.0
    # Both signals corroborating each other should push confidence high.
    assert agent_result["confidence"] >= 0.9


def test_confidence_lower_when_metric_signal_has_no_log_corroboration(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(severity="high", z_score=3.1)])
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    state = {"user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_agent(state)

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["metric_signal"]["has_signal"] is True
    assert agent_result["raw_data"]["log_signal"]["has_signal"] is False
    # Corroborated (previous test) should score strictly higher than
    # uncorroborated, for the same underlying severity ballpark.
    assert agent_result["confidence"] < 0.9


def test_confidence_low_when_no_evidence_at_all(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [])
    monkeypatch.setattr(anomaly, "collect_metrics", lambda: [
        {**{k: 5.0 for k in ("cpu_percent", "memory_percent", "disk_percent")},
         "instance": NODE["instance"], "status": "up", "health": "healthy"},
    ])
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    state = {"user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_agent(state)

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["metric_signal"]["has_signal"] is False
    assert agent_result["raw_data"]["log_signal"]["has_signal"] is False
    assert agent_result["confidence"] <= 0.3
    assert "No current metric or log evidence" in agent_result["summary"]


# --------------------------------------------------------------------
# Metric-check sub-step tiers
# --------------------------------------------------------------------

def test_metric_check_falls_back_to_live_read_when_no_flag(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [])
    monkeypatch.setattr(anomaly, "collect_metrics", lambda: [
        {"instance": NODE["instance"], "cpu_percent": 96.4, "memory_percent": 40.0,
         "disk_percent": 30.0, "status": "up", "health": "critical"},
    ])

    signal = anomaly._check_metrics(NODE)

    assert signal["has_signal"] is True
    assert signal["data"]["source"] == "live_metrics"
    assert signal["data"]["health"] == "critical"


def test_metric_check_prefers_flagged_anomaly_over_live_read(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])
    # Even if collect_metrics() were called, a scored flag should win --
    # assert it's not even consulted.
    monkeypatch.setattr(anomaly, "collect_metrics", lambda: (_ for _ in ()).throw(AssertionError("should not be called")))

    signal = anomaly._check_metrics(NODE)

    assert signal["data"]["source"] == "anomaly_flags"


def test_metric_check_picks_worst_severity_among_multiple_flags(monkeypatch):
    monkeypatch.setattr(
        anomaly.crud,
        "list_open_anomaly_flags",
        lambda db, hostname: [_flag(metric_name="ram_usage", severity="medium"), _flag(metric_name="cpu_usage", severity="critical")],
    )

    signal = anomaly._check_metrics(NODE)

    assert signal["data"]["metric_name"] == "cpu_usage"
    assert signal["data"]["severity"] == "critical"
    assert signal["data"]["other_flagged_metrics"] == ["ram_usage"]


def test_metric_check_no_signal_when_nothing_flagged_or_abnormal(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [])
    monkeypatch.setattr(anomaly, "collect_metrics", lambda: [
        {"instance": NODE["instance"], "cpu_percent": 5.0, "memory_percent": 5.0,
         "disk_percent": 5.0, "status": "up", "health": "healthy"},
    ])

    signal = anomaly._check_metrics(NODE)

    assert signal["has_signal"] is False
    assert signal["data"] is None


# --------------------------------------------------------------------
# Log-check sub-step
# --------------------------------------------------------------------

def test_log_check_returns_no_signal_on_empty_result(monkeypatch):
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    signal = anomaly._check_logs(NODE, {"has_signal": False, "detail": "", "data": None})

    assert signal["has_signal"] is False
    assert signal["entries"] == []


def test_log_check_degrades_gracefully_when_loki_unreachable(monkeypatch):
    def boom(*a, **k):
        raise Exception("connection refused")

    monkeypatch.setattr(loki_client, "query_range", boom)

    signal = anomaly._check_logs(NODE, {"has_signal": False, "detail": "", "data": None})

    assert signal["has_signal"] is False
    # v0.5: a failed check is tagged distinctly from "checked, found nothing"
    # (has_signal False either way, but degraded/failure only set here) --
    # see resilience.py / adr-0007.
    assert signal["degraded"] is True
    assert signal["failure"]["source"] == "anomaly.loki"
    assert signal["failure"]["error_type"] == "Exception"
    assert "couldn't complete" in signal["detail"].lower()


def test_anomaly_agent_pushes_loki_failure_into_state_failures_and_caps_confidence(monkeypatch):
    # A confidently-flagged critical metric signal would normally push
    # confidence to 0.95 -- degraded log evidence should cap it well below
    # that, not push it even higher the way real corroboration would.
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(severity="critical")])

    def boom(*a, **k):
        raise Exception("connection refused")

    monkeypatch.setattr(loki_client, "query_range", boom)

    state = {"user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_agent(state)

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["log_signal"]["degraded"] is True
    assert agent_result["confidence"] <= anomaly._DEGRADED_LOG_CONFIDENCE_CAP
    assert "reduced confidence" in agent_result["summary"].lower() or "couldn't complete" in agent_result["summary"].lower()

    assert len(result["failures"]) == 1
    assert result["failures"][0]["source"] == "anomaly.loki"


def test_log_check_sorts_entries_newest_first(monkeypatch):
    monkeypatch.setattr(
        loki_client,
        "query_range",
        lambda *a, **k: _loki_stream([
            (1756123100000000000, "older ERROR line"),
            (1756123200000000000, "newer ERROR line"),
        ]),
    )

    signal = anomaly._check_logs(NODE, {"has_signal": False, "detail": "", "data": None})

    assert signal["has_signal"] is True
    assert signal["entries"][0]["line"] == "newer ERROR line"


# --------------------------------------------------------------------
# Cause hypothesis (derived from the two sub-steps' own output)
# --------------------------------------------------------------------

def test_agent_surfaces_a_hedged_cause_hypothesis_from_log_content(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(metric_name="ram_usage")])
    monkeypatch.setattr(
        loki_client,
        "query_range",
        lambda *a, **k: _loki_stream([
            (1756123200000000000, "2026-08-25T12:00:00Z ERROR nova-compute Out of memory: Kill process 4821"),
        ]),
    )

    state = {"user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_agent(state)

    likely_cause = result["agent_result"]["raw_data"]["likely_cause"]
    assert likely_cause is not None
    assert "memory" in likely_cause.lower()
    # The narrative should actually use the hypothesis, hedged rather than asserted as fact.
    summary = result["agent_result"]["summary"]
    assert "memory" in summary.lower()
    assert "consistent with" in summary.lower() or "hypothesis" in summary.lower()


def test_log_content_hypothesis_takes_priority_over_metric_only_hint(monkeypatch):
    # cpu_usage alone would suggest a "runaway process" hint, but a log line
    # about a disk-full condition is more specific and should win.
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(metric_name="cpu_usage")])
    monkeypatch.setattr(
        loki_client,
        "query_range",
        lambda *a, **k: _loki_stream([(1756123200000000000, "ERROR cinder-volume No space left on device")]),
    )

    signal_metric = anomaly._check_metrics(NODE)
    signal_log = anomaly._check_logs(NODE, signal_metric)
    cause = anomaly._hypothesize_cause(signal_metric, signal_log)

    assert cause is not None and "disk" in cause.lower()


def test_metric_only_hypothesis_when_logs_dont_narrow_it_down(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag(metric_name="cpu_usage")])
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    signal_metric = anomaly._check_metrics(NODE)
    signal_log = anomaly._check_logs(NODE, signal_metric)
    cause = anomaly._hypothesize_cause(signal_metric, signal_log)

    assert cause is not None and "cpu" in cause.lower()


def test_no_hypothesis_when_no_evidence_at_all(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [])
    monkeypatch.setattr(anomaly, "collect_metrics", lambda: [
        {"instance": NODE["instance"], "cpu_percent": 5.0, "memory_percent": 5.0,
         "disk_percent": 5.0, "status": "up", "health": "healthy"},
    ])
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])

    signal_metric = anomaly._check_metrics(NODE)
    signal_log = anomaly._check_logs(NODE, signal_metric)
    cause = anomaly._hypothesize_cause(signal_metric, signal_log)

    assert cause is None


def test_fallback_summary_is_a_developed_multi_sentence_narrative(monkeypatch):
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])
    monkeypatch.setattr(
        loki_client,
        "query_range",
        lambda *a, **k: _loki_stream([(1756123200000000000, "ERROR Lost connection to libvirt")]),
    )

    state = {"user_query": "something's wrong with compute-02", "known_nodes": KNOWN_NODES}
    result = anomaly.anomaly_agent(state)
    summary = result["agent_result"]["summary"]

    # Short one-liners fail this: the merged narrative should read as
    # several developed sentences, not a terse status line.
    sentence_count = summary.count(". ") + 1
    assert sentence_count >= 4
    assert "next step" in summary.lower() or "recommended" in summary.lower()


# --------------------------------------------------------------------
# Node resolution error path (shared contract with monitoring/prediction)
# --------------------------------------------------------------------

def test_anomaly_agent_errors_when_node_cannot_be_resolved():
    state = {"user_query": "is something wrong?", "known_nodes": [
        {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"},
        {"hostname": "storage-09", "role": "storage", "instance": "10.0.2.9:9100"},
    ]}

    result = anomaly.anomaly_agent(state)

    assert result["agent_result"] is None
    assert "couldn't tell which node" in result["error"].lower()
