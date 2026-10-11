"""Phase 4b: container-level health in the Living Model
(services/container_health.py, adr-0012).

The bug this pins: a Kolla container going down -- above all one that is not
an OpenStack API service, like rabbitmq -- produced no signal in the twin.
Prometheus and Neo4j are faked at their own call sites, as in
test_prometheus_health.py.
"""
import pytest

import app.agents.nodes.monitoring as monitoring
import app.services.container_health as ch
from app.services.prometheus_health import SERVICE_STATE_SEVERITY, reconcile_service_state

NOW = 1_700_000_000.0


def _obs(host="controller-01", containers=None, scraped_at=NOW - 10):
    return {host: {"scraped_at": scraped_at, "containers": containers or {}}}


def _c(state="running", restarts=0):
    return {"state": state, "docker_state": state, "restart_count": restarts}


# --------------------------------------------------------------------
# State normalisation
# --------------------------------------------------------------------

@pytest.mark.parametrize(
    "docker_state, health, expected",
    [
        ("running", "healthy", ch.RUNNING),
        ("running", "none", ch.RUNNING),
        ("running", None, ch.RUNNING),
        ("running", "unhealthy", ch.UNHEALTHY),
        ("restarting", None, ch.RESTARTING),
        ("exited", None, ch.DOWN),
        ("dead", None, ch.DOWN),
        ("created", None, ch.DOWN),
        ("paused", None, ch.DOWN),
        ("weird", None, ch.UNKNOWN),
        (None, None, ch.UNKNOWN),
    ],
)
def test_docker_state_is_normalised(docker_state, health, expected):
    assert ch.normalize_state(docker_state, health) == expected


def test_kolla_container_names_map_to_openstack_binaries():
    assert ch.container_binary("nova_compute") == "nova-compute"
    assert ch.container_binary("neutron_openvswitch_agent") == "neutron-openvswitch-agent"
    assert ch.container_binary("rabbitmq") == "rabbitmq"


# --------------------------------------------------------------------
# build_container_rows
# --------------------------------------------------------------------

def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_a_stopped_container_is_reported_down_and_a_running_one_running():
    rows = _by_name(ch.build_container_rows(
        _obs(containers={"rabbitmq": _c("down"), "mariadb": _c("running")}), {}, now=NOW,
    ))
    assert rows["rabbitmq"]["state"] == ch.DOWN
    assert rows["mariadb"]["state"] == ch.RUNNING
    assert rows["rabbitmq"]["id"] == "rabbitmq@controller-01"


def test_stale_telemetry_is_unknown_never_carried_forward_as_running():
    rows = _by_name(ch.build_container_rows(
        _obs(containers={"rabbitmq": _c("running")}, scraped_at=NOW - 3600), {}, now=NOW, stale_after=180,
    ))
    assert rows["rabbitmq"]["state"] == ch.UNKNOWN


def test_a_known_container_absent_from_a_fresh_scrape_is_missing():
    rows = _by_name(ch.build_container_rows(
        _obs(containers={"mariadb": _c()}), {"controller-01": {"mariadb", "rabbitmq"}}, now=NOW,
    ))
    assert rows["rabbitmq"]["state"] == ch.MISSING


def test_a_known_container_absent_from_a_stale_scrape_is_unknown_not_missing():
    rows = _by_name(ch.build_container_rows(
        _obs(containers={"mariadb": _c()}, scraped_at=NOW - 3600),
        {"controller-01": {"mariadb", "rabbitmq"}}, now=NOW,
    ))
    assert rows["rabbitmq"]["state"] == ch.UNKNOWN


def test_a_host_with_no_series_at_all_is_unknown_not_forty_missing_containers():
    rows = ch.build_container_rows({}, {"compute-09": {"nova_compute", "nova_libvirt"}}, now=NOW)
    assert {r["state"] for r in rows} == {ch.UNKNOWN}
    assert len(rows) == 2


def test_first_ever_scrape_has_nothing_to_call_missing():
    rows = ch.build_container_rows(_obs(containers={"rabbitmq": _c()}), {}, now=NOW)
    assert [r["state"] for r in rows] == [ch.RUNNING]


# --------------------------------------------------------------------
# alert_rows
# --------------------------------------------------------------------

def _rows(**states):
    return [
        {"id": f"{name}@h", "name": name, "host": "h", "binary": ch.container_binary(name), "state": state}
        for name, state in states.items()
    ]


def test_an_unmapped_container_down_raises_a_down_alert_row():
    out = ch.alert_rows(_rows(rabbitmq=ch.DOWN, mariadb=ch.MISSING), {})
    assert {r["service_id"]: r["state"] for r in out} == {"rabbitmq@h": "down", "mariadb@h": "down"}


def test_healthy_containers_emit_up_rows_so_open_alerts_resolve():
    out = ch.alert_rows(_rows(rabbitmq=ch.RUNNING), {})
    assert out == [{"service_id": "rabbitmq@h", "state": "up"}]


def test_unknown_emits_nothing_so_an_open_alert_is_left_exactly_as_it_was():
    assert ch.alert_rows(_rows(rabbitmq=ch.UNKNOWN), {}) == []


def test_unhealthy_is_a_high_severity_alert():
    out = ch.alert_rows(_rows(rabbitmq=ch.UNHEALTHY), {})
    assert out == [{"service_id": "rabbitmq@h", "state": "unhealthy"}]
    assert SERVICE_STATE_SEVERITY["unhealthy"] == "high"


def test_a_container_that_implements_a_service_is_not_double_alerted_when_down():
    # The Service's reconciled state already goes "down" (one alert, earlier
    # than OpenStack's heartbeat); a second container-level alert for the
    # same incident would be noise.
    out = ch.alert_rows(_rows(nova_compute=ch.DOWN), {"nova_compute@h": "nova-compute@h"})
    assert out == [{"service_id": "nova_compute@h", "state": "up"}]


def test_a_mapped_container_that_is_merely_unhealthy_still_alerts_on_its_own():
    out = ch.alert_rows(_rows(nova_compute=ch.UNHEALTHY), {"nova_compute@h": "nova-compute@h"})
    assert out == [{"service_id": "nova_compute@h", "state": "unhealthy"}]


# --------------------------------------------------------------------
# Service-state reconciliation
# --------------------------------------------------------------------

@pytest.mark.parametrize("container_state", ["down", "missing", "restarting"])
def test_openstack_up_but_its_container_is_dead_reconciles_to_down(container_state):
    assert reconcile_service_state("up", "up", container_state) == "down"


@pytest.mark.parametrize("container_state", [None, "running", "unhealthy", "unknown"])
def test_no_container_problem_leaves_the_existing_result_unchanged(container_state):
    assert reconcile_service_state("up", "up", container_state) == "up"


def test_a_down_host_still_wins_over_the_container_signal():
    assert reconcile_service_state("up", "down", "down") == "unreachable"


def test_openstacks_own_down_is_never_overridden_by_a_running_container():
    assert reconcile_service_state("down", "up", "running") == "down"
    assert reconcile_service_state("disabled", "up", "down") == "disabled"


def test_the_cypher_reconciliation_mirrors_the_python_one():
    import inspect
    import app.services.prometheus_health as ph

    source = inspect.getsource(ph._sync_service_state_to_graph)
    for state in sorted(ch.SERVICE_DOWN_STATES):
        assert f"'{state}'" in source
    assert "'unhealthy'" not in source  # unhealthy must not take a service down


def test_monitoring_and_container_health_agree_on_what_a_problem_state_is():
    assert monitoring._CONTAINER_PROBLEM_STATES == ch.PROBLEM_STATES


# --------------------------------------------------------------------
# Prometheus read
# --------------------------------------------------------------------

def _sample(host, container=None, value="1", **labels):
    metric = {"node": host, **labels}
    if container:
        metric["container"] = container
    return {"metric": metric, "value": [NOW, value]}


def test_fetch_observations_assembles_state_restarts_and_scrape_time(monkeypatch):
    answers = {
        ch.CONTAINER_UP_QUERY: [
            _sample("controller-01", "rabbitmq", "0", state="exited", health="none"),
            _sample("controller-01", "mariadb", "1", state="running", health="unhealthy"),
            _sample("controller-01", None, "1", state="running"),  # no container label: ignored
            {"metric": {"container": "x", "state": "running"}, "value": [NOW, "1"]},  # no node label: ignored
        ],
        ch.CONTAINER_RESTARTS_QUERY: [_sample("controller-01", "rabbitmq", "3")],
        ch.SCRAPE_TIMESTAMP_QUERY: [_sample("controller-01", None, str(NOW - 5))],
    }
    monkeypatch.setattr(ch, "query", lambda q: answers[q])

    obs = ch.fetch_observations()

    assert set(obs) == {"controller-01"}
    assert obs["controller-01"]["scraped_at"] == NOW - 5
    assert obs["controller-01"]["containers"]["rabbitmq"] == {
        "state": ch.DOWN, "docker_state": "exited", "restart_count": 3,
    }
    assert obs["controller-01"]["containers"]["mariadb"]["state"] == ch.UNHEALTHY
    assert set(obs["controller-01"]["containers"]) == {"rabbitmq", "mariadb"}


# --------------------------------------------------------------------
# sync_container_health (Prometheus + graph faked)
# --------------------------------------------------------------------

class _Session:
    def __init__(self, log, known=None, service_map=None):
        self.log, self.known, self.service_map = log, known or [], service_map or []

    def run(self, query, **params):
        self.log.append((" ".join(query.split()), params))
        if "RETURN k.host AS host" in query:
            return self.known
        if "RETURN k.id AS container_id" in query:
            return self.service_map
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


def _patch_graph(monkeypatch, known=None, service_map=None):
    log: list = []
    monkeypatch.setattr(ch.graph_db, "driver", _Driver(_Session(log, known, service_map)))
    return log


def test_a_dead_rabbitmq_container_lands_in_the_graph_and_raises_an_alert(monkeypatch):
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch, "fetch_observations", lambda: _obs(containers={
        "rabbitmq": _c("down", restarts=3), "mariadb": _c("running"),
    }))
    log = _patch_graph(monkeypatch)

    result = ch.sync_container_health()

    assert result["queried"] is True and result["containers"] == 2 and result["problems"] == 1
    assert {"service_id": "rabbitmq@controller-01", "state": "down"} in result["alert_rows"]
    assert {"service_id": "mariadb@controller-01", "state": "up"} in result["alert_rows"]
    merge = next(params for query, params in log if "MERGE (k:Container" in query)
    written = {r["name"]: r for r in merge["rows"]}
    assert written["rabbitmq"]["state"] == ch.DOWN and written["rabbitmq"]["restart_count"] == 3
    assert any("MERGE (s)-[:RUNS_IN]->(k)" in query for query, _ in log)


def test_a_container_that_disappeared_since_the_last_pass_becomes_missing(monkeypatch):
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch, "fetch_observations", lambda: _obs(containers={"mariadb": _c()}))
    log = _patch_graph(monkeypatch, known=[{"host": "controller-01", "name": "rabbitmq"},
                                           {"host": "controller-01", "name": "mariadb"}])

    result = ch.sync_container_health()

    assert {"service_id": "rabbitmq@controller-01", "state": "down"} in result["alert_rows"]
    merge = next(params for query, params in log if "MERGE (k:Container" in query)
    assert {r["name"]: r["state"] for r in merge["rows"]}["rabbitmq"] == ch.MISSING


def test_an_unreachable_prometheus_skips_the_pass_and_touches_nothing(monkeypatch):
    def boom():
        raise ConnectionError("prometheus down")

    monkeypatch.setattr(ch, "fetch_observations", boom)
    log = _patch_graph(monkeypatch)

    result = ch.sync_container_health()

    assert result == {"queried": False, "containers": 0, "problems": 0, "alert_rows": []}
    assert log == []


def test_a_graph_failure_skips_the_pass_without_raising(monkeypatch):
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch, "fetch_observations", lambda: _obs(containers={"rabbitmq": _c("down")}))

    class _Broken:
        def session(self):
            raise RuntimeError("neo4j down")

    monkeypatch.setattr(ch.graph_db, "driver", _Broken())

    assert ch.sync_container_health()["queried"] is False


# --------------------------------------------------------------------
# Monitoring agent surfaces what used to be invisible
# --------------------------------------------------------------------

def _containers():
    return [
        {"name": "rabbitmq", "host": "controller-01", "state": "down", "service_id": None},
        {"name": "nova_compute", "host": "compute-01", "state": "down", "service_id": "nova-compute@compute-01"},
        {"name": "mariadb", "host": "controller-01", "state": "running", "service_id": None},
        {"name": "haproxy", "host": "compute-02", "state": "unhealthy", "service_id": None},
    ]


def test_monitoring_lists_non_service_containers_in_a_problem_state(monkeypatch):
    monkeypatch.setattr(monitoring.graph_db, "fetch_containers", _containers)
    rows = monitoring._problem_containers(None, [])
    # nova_compute is covered by its Service row; mariadb is healthy.
    assert {r["name"] for r in rows} == {"rabbitmq", "haproxy"}


def test_monitoring_scopes_problem_containers_to_the_node(monkeypatch):
    monkeypatch.setattr(monitoring.graph_db, "fetch_containers", _containers)
    rows = monitoring._problem_containers({"hostname": "controller-01"}, [])
    assert [r["name"] for r in rows] == ["rabbitmq"]


def test_monitoring_does_not_mix_unrelated_containers_into_a_service_specific_question(monkeypatch):
    monkeypatch.setattr(monitoring.graph_db, "fetch_containers", _containers)
    assert monitoring._problem_containers(None, ["nova-compute"]) == []


def test_a_graph_failure_reading_containers_never_costs_the_services_answer(monkeypatch):
    def boom():
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(monitoring.graph_db, "fetch_containers", boom)
    assert monitoring._problem_containers(None, []) == []
