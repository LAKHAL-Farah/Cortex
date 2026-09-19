"""v1.2 -- parallel incident investigation: anomaly / network / security run
concurrently, arbitration picks the best-supported theory and says why, and
the OpenStack expert is then chained on *that theory's* evidence.

No Postgres/Loki/Prometheus/NVIDIA API: the three per-node `_investigate`
functions are replaced by fakes. The concurrency assertions use a
`threading.Barrier`, so they can only pass if the branches really run at the
same time -- a serialized fan-out would time the barrier out."""
import threading
import time
from types import SimpleNamespace

import pytest

import app.agents.intent_router as intent_router
import app.agents.nodes.anomaly as anomaly
import app.agents.nodes.network as network
import app.agents.nodes.security as security
from app.agents import incident_fanout
from app.agents.graph import app_graph
from app.services import security_rbac

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
OTHER = {"hostname": "compute-01", "role": "compute", "instance": "10.0.1.11:9100"}
KNOWN = [OTHER, NODE]


# ------------------------------------------------------------------ unit ----

def _finding(agent, host="compute-02", start=0.0, end=1.0, conf=0.7, signal=True, failed=False):
    raw = {"hostname": host, "has_signal": signal}
    if failed:
        raw["error"] = {"error_type": "TimeoutError"}
    return {
        "hostname": host, "agent": agent, "failures": [],
        "agent_result": {"summary": f"{agent} says", "confidence": conf, "raw_data": raw},
        "timing": {"start": start, "end": end, "duration_ms": (end - start) * 1000, "thread": f"t-{agent}", "started_at": "x"},
    }


def test_parallelism_reports_overlap_and_speedup():
    p = incident_fanout.summarize_parallelism(
        [_finding("anomaly", start=0.0, end=1.0), _finding("network", start=0.05, end=1.1), _finding("security", start=0.1, end=0.9)]
    )
    assert p["peak_concurrency"] == 3 and p["concurrent"] and p["threads"] == 3
    assert p["wall_ms"] == pytest.approx(1100, abs=1)
    assert p["sequential_ms"] == pytest.approx(2850, abs=1)
    assert p["speedup"] == pytest.approx(2.6, abs=0.1)
    assert [b["agent"] for b in p["branches"]] == ["anomaly", "network", "security"]


def test_back_to_back_branches_are_not_counted_as_concurrent():
    p = incident_fanout.summarize_parallelism(
        [_finding("anomaly", start=0.0, end=1.0), _finding("network", start=1.0, end=2.0)]
    )
    assert p["peak_concurrency"] == 1 and not p["concurrent"]


def test_parallelism_is_none_without_timing_stamps():
    f = _finding("anomaly")
    del f["timing"]
    assert incident_fanout.summarize_parallelism([f]) is None


def test_timed_branch_stamps_every_finding_and_logs_start_and_done(caplog):
    @incident_fanout.timed_branch("network")
    def branch(payload):
        return {"agent_results": [{"hostname": payload["node"]["hostname"], "agent": "network"}]}

    with caplog.at_level("INFO", logger="app.agents.incident_fanout"):
        out = branch({"node": NODE})

    timing = out["agent_results"][0]["timing"]
    assert timing["thread"] == threading.current_thread().name and timing["end"] >= timing["start"]
    text = caplog.text
    assert "network branch START host=compute-02" in text and "network branch DONE" in text


def test_ledger_and_explanation_name_the_winner_and_the_corroboration():
    findings = [_finding("anomaly", conf=0.7), _finding("network", conf=0.8), _finding("security", conf=0.9, signal=False)]
    winner = findings[1]
    ledger = incident_fanout.theory_ledger(
        findings, winner,
        has_signal=lambda f: f["agent_result"]["raw_data"]["has_signal"],
        bonus=lambda f: 0.05,
        score=lambda f: f["agent_result"]["confidence"] + (0.05 if f["agent_result"]["raw_data"]["has_signal"] else 0),
    )
    by_agent = {r["agent"]: r for r in ledger}
    assert by_agent["network"]["verdict"] == "winner" and by_agent["anomaly"]["verdict"] == "also_flagged"
    assert by_agent["security"]["verdict"] == "no_signal"
    assert ledger[0]["agent"] == "network"  # a confident "nothing found" never outranks a signal
    why = incident_fanout.explain_decision("compute-02", ledger)
    assert "network agent has the best-supported theory" in why and "anomaly also flagged" in why
    assert "No signal from security" in why


def test_a_timed_out_branch_shows_up_as_failed_not_as_a_quiet_agent():
    findings = [_finding("anomaly"), _finding("network", conf=0.0, signal=False, failed=True)]
    ledger = incident_fanout.theory_ledger(
        findings, findings[0], has_signal=lambda f: f["agent_result"]["raw_data"]["has_signal"],
        bonus=lambda f: 0.0, score=lambda f: f["agent_result"]["confidence"],
    )
    assert {r["agent"]: r["verdict"] for r in ledger} == {"anomaly": "winner", "network": "failed"}


# ------------------------------------------------------------ full graph ----

def _route_to_anomaly(monkeypatch):
    class _Structured:
        def invoke(self, messages):
            return SimpleNamespace(agent="anomaly", confidence=0.95)

    class _LLM:
        def with_structured_output(self, schema):
            return _Structured()

    monkeypatch.setattr(intent_router, "get_chat_model", lambda **k: _LLM())


def _no_llm_narration(monkeypatch):
    def _raise(**kwargs):
        raise anomaly.LLMConfigError("no key")
    monkeypatch.setattr(anomaly, "get_chat_model", _raise)


def _fake_investigations(monkeypatch, *, anomaly_conf=0.7, network_conf=0.8, security_conf=0.9,
                         anomaly_signal=True, network_signal=True, security_signal=False, sleep=0.1):
    """All three fakes rendezvous on one barrier: they finish only if all three
    are running at once."""
    barrier = threading.Barrier(3, timeout=5)

    def _wrap(make):
        def fake(query, node):
            barrier.wait()
            time.sleep(sleep)
            return make(node), []
        return fake

    def anomaly_result(node):
        return {
            "summary": "compute-02 CPU is spiking.", "confidence": anomaly_conf,
            "raw_data": {
                "hostname": node["hostname"], "role": node["role"], "has_signal": anomaly_signal,
                "metric_signal": {"has_signal": anomaly_signal, "detail": "CPU anomaly at 97.3%",
                                  "data": {"source": "anomaly_flags", "metric_name": "cpu_usage"}},
                "log_signal": {"has_signal": False, "entries": []}, "likely_cause": None,
            },
        }

    def network_result(node):
        return {
            "summary": "neutron-l3-agent is down on compute-02.", "confidence": network_conf,
            "raw_data": {
                "hostname": node["hostname"], "role": node["role"], "has_signal": network_signal, "scope": "node",
                "metric_signal": {"has_signal": False},
                "neutron_signal": {"has_signal": network_signal, "detail": "neutron-l3-agent is down",
                                   "down_agents": [{"binary": "neutron-l3-agent"}] if network_signal else []},
            },
        }

    def security_result(node):
        return {
            "summary": "CVE-2026-0001 present on compute-02.", "confidence": security_conf,
            "raw_data": {"hostname": node["hostname"], "role": node["role"], "has_signal": security_signal,
                         "cve_signal": {"has_signal": security_signal, "detail": "CVE-2026-0001"}},
        }

    monkeypatch.setattr(anomaly, "_investigate", _wrap(anomaly_result))
    monkeypatch.setattr(network, "_investigate", _wrap(network_result))
    monkeypatch.setattr(security, "_investigate", _wrap(security_result))


def _run(monkeypatch, query="what is wrong with compute-02"):
    _route_to_anomaly(monkeypatch)
    _no_llm_narration(monkeypatch)
    return app_graph.invoke({"user_query": query, "known_nodes": KNOWN, "failures": [], "agent_results": [], "trace_events": []})


def test_agents_really_run_concurrently_and_the_join_reports_it(monkeypatch, caplog):
    _fake_investigations(monkeypatch)
    with caplog.at_level("INFO"):
        result = _run(monkeypatch)

    assert not result.get("failures"), "a serialized fan-out would have broken the barrier"
    arb = result["agent_result"]["raw_data"]["arbitration"]
    par = arb["parallelism"]
    assert par["peak_concurrency"] == 3 and par["threads"] == 3 and par["concurrent"]
    assert par["wall_ms"] < par["sequential_ms"]
    assert "incident fan-out joined: 3 branches on 3 threads, peak concurrency 3" in caplog.text
    assert caplog.text.count("branch START") == 3

    trace_step = next(e for e in result["trace_events"] if e["node"] == "anomaly")
    assert trace_step["detail"]["parallelism"]["peak_concurrency"] == 3
    # every investigating agent is on the step, best-supported first, without numbers
    investigated = trace_step["detail"]["investigated"]
    assert {i["agent"] for i in investigated} == {"anomaly", "network", "security"}
    assert investigated[0]["verdict"] == "winner"
    assert all(set(i) == {"agent", "verdict", "has_signal"} for i in investigated)


def test_best_supported_theory_wins_then_the_expert_runs_on_its_evidence(monkeypatch):
    _fake_investigations(monkeypatch)  # network 0.8 + corroboration beats anomaly 0.7; security found nothing
    result = _run(monkeypatch)

    raw = result["agent_result"]["raw_data"]
    assert result["target_agent"] == "openstack_expert"
    assert raw["diagnosed_by"] == "network" and raw["investigating_agent"] == "network"
    assert raw["corroborated_by"] == ["anomaly"]
    assert raw["arbitration"]["winner"] == "network"
    verdicts = {t["agent"]: t["verdict"] for t in raw["arbitration"]["theories"]}
    assert verdicts == {"network": "winner", "anomaly": "also_flagged", "security": "no_signal"}
    # security's numbers are not on the chained answer's raw_data
    assert next(t for t in raw["arbitration"]["theories"] if t["agent"] == "security").get("restricted")
    assert "supporting_evidence" not in raw and "cross_agent_findings" not in raw

    answer = result["final_answer"]
    assert answer.index("### Incident analysis") < answer.index("neutron-l3-agent")
    assert "in parallel" in answer and "Best-supported theory" in answer
    assert "CVE-2026-0001" not in answer  # analysis section never quotes a security finding
    assert raw["matched_symptom_id"]  # a real runbook was walked through
    # ...and it is the winner's (Neutron agent down), not the corroborating CPU anomaly's
    assert "neutron" in raw["matched_symptom_id"] and "cpu" not in raw["matched_symptom_id"]
    assert "Corroborated by the anomaly agent" in answer
    assert result["critic_verdict"]["status"] == "pass", result["critic_verdict"]


def test_a_security_win_is_not_chained_into_the_expert(monkeypatch):
    _fake_investigations(monkeypatch, anomaly_signal=False, network_signal=False, security_signal=True,
                         security_conf=0.9, anomaly_conf=0.5, network_conf=0.5)
    result = _run(monkeypatch)

    assert result["target_agent"] == "anomaly"  # no chain
    raw = result["agent_result"]["raw_data"]
    assert raw["investigating_agent"] == "security" and raw["arbitration"]["winner_summary"] is None
    assert "supporting_evidence" not in raw


def test_a_viewer_gets_no_security_numbers_from_the_arbitration_block(monkeypatch):
    _fake_investigations(monkeypatch, anomaly_signal=False, network_signal=False, security_signal=True)
    result = _run(monkeypatch)
    raw = result["agent_result"]["raw_data"]

    answer, redacted = security_rbac.filter_security_response_for_role(result["final_answer"], raw, "anomaly", "viewer")
    assert answer == security_rbac.RESTRICTED_NOTICE
    sec = next(t for t in redacted["arbitration"]["theories"] if t["agent"] == "security")
    assert set(sec) == {"agent", "has_signal", "verdict", "restricted"}
    # admins still see everything
    _a, admin_raw = security_rbac.filter_security_response_for_role(result["final_answer"], raw, "anomaly", "admin")
    assert next(t for t in admin_raw["arbitration"]["theories"] if t["agent"] == "security")["confidence"] > 0


def test_a_slow_branch_does_not_lose_the_others(monkeypatch):
    _fake_investigations(monkeypatch)

    def boom(query, node):
        raise RuntimeError("loki exploded")

    # replace one agent by a crash *after* it reaches the barrier's peers
    monkeypatch.setattr(security, "_investigate", boom)
    monkeypatch.setattr(anomaly, "_investigate", lambda q, n: ({"summary": "s", "confidence": 0.7, "raw_data": {
        "hostname": n["hostname"], "role": "compute", "has_signal": True,
        "metric_signal": {"has_signal": True, "detail": "d", "data": {"source": "anomaly_flags", "metric_name": "cpu_usage"}},
        "log_signal": {"has_signal": False, "entries": []}, "likely_cause": None}}, []))
    monkeypatch.setattr(network, "_investigate", lambda q, n: ({"summary": "s", "confidence": 0.6, "raw_data": {
        "hostname": n["hostname"], "role": "compute", "has_signal": False, "scope": "node",
        "metric_signal": {"has_signal": False}, "neutron_signal": {"has_signal": False}}}, []))
    result = _run(monkeypatch)

    theories = {t["agent"]: t["verdict"] for t in result["agent_result"]["raw_data"]["arbitration"]["theories"]} \
        if "arbitration" in result["agent_result"]["raw_data"] else {}
    assert theories.get("security") == "failed"
    assert theories.get("anomaly") in {"winner", "also_flagged"}
    assert result["failures"]


def test_corroborating_evidence_fills_a_gap_when_the_winners_evidence_matches_no_runbook(monkeypatch):
    # Network wins on a Neutron signal that names no down agent (nothing to match a
    # runbook on); the anomaly agent's CPU evidence on the same host then supplies the match.
    _fake_investigations(monkeypatch)
    real = network._investigate

    def vague_network(query, node):
        result, failures = real(query, node)
        result["raw_data"]["neutron_signal"]["down_agents"] = []
        return result, failures

    monkeypatch.setattr(network, "_investigate", vague_network)
    result = _run(monkeypatch)

    raw = result["agent_result"]["raw_data"]
    assert result["target_agent"] == "openstack_expert"
    assert raw["diagnosed_by"] == "network" and raw["corroborated_by"] == ["anomaly"]
    assert "cpu" in raw["matched_symptom_id"]
