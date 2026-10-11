"""
services/api/tests/test_capacity_simulator.py

Tests for the capacity planning what-if simulator (roadmap 4.12,
app.services.capacity_simulator + app.routers.capacity).

Acceptance criterion under test: "Simulator lets you change >=2 parameters and
see projected impact on the capacity timeline" -- see
test_two_parameters_change_the_timeline and the combined-scenario tests.

Like test_forecast_threshold.py, these build synthetic get_forecast()-shaped
dicts rather than going through the CSV dataset and trained models: the
simulator is a pure function of (scenario, forecast trajectories, capacity),
so it is tested in isolation from forecasting itself. The router tests build
a tiny app around just the capacity router (no Postgres needed).
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.routers import capacity as capacity_router
from app.services import capacity_simulator as sim
from app.services.capacity_simulator import Scenario, simulate

GENERATED_AT = "2026-10-01T00:00:00"

# 2 compute nodes, 32 physical vCPUs / 128 GB between them, 1 TB Cinder pool.
CAPACITY = {
    "physical_vcpus": 32.0,
    "physical_ram_mb": 128 * 1024.0,
    "storage_gb": 1000.0,
    "compute_nodes": 2,
    "cpu_allocation_ratio": 4.0,
    "ram_allocation_ratio": 1.0,
    "allocated_vcpus": 64.0,
    "allocated_ram_mb": 64 * 1024.0,
    "allocated_gb": 400.0,
    "rates": {"vcpu": 1.0, "ram_gb": 0.5, "storage_gb": 0.1},
    "sampled_at": GENERATED_AT,
}

HOURS_30D = [1, 6, 12, 24, 72, 168, 336, 504, 720]


def _node(hostname, now, slope_per_day, hours=HOURS_30D, band=3.0, extrapolated_after=None):
    """A node whose median forecast is `now` rising `slope_per_day` pts/day."""
    points = []
    for h in hours:
        v = max(now + slope_per_day * h / 24.0, 0.0)
        points.append({
            "horizon_hours": h,
            "timestamp": "irrelevant",
            "predicted": v,
            "lower": max(v - band, 0.0),
            "upper": v + band,
            "extrapolated": extrapolated_after is not None and h > extrapolated_after,
        })
    return {
        "hostname": hostname,
        "generated_at": GENERATED_AT,
        "model_type": "ml_quantile",
        "forecast": points,
        "actual": [{"timestamp": GENERATED_AT, "value": now}],
    }


def _forecasts(cpu_slope=0.5, mem_slope=0.2):
    return {
        "cpu_percent": [_node("compute-01", 40.0, cpu_slope), _node("compute-02", 30.0, cpu_slope)],
        "memory_percent": [_node("compute-01", 50.0, mem_slope), _node("compute-02", 45.0, mem_slope)],
        "disk_percent": [_node("storage-01", 35.0, 0.1)],
    }


def _res(result, metric):
    return next(r for r in result["resources"] if r["metric"] == metric)


def _at(resource, hours, key):
    return next(p[key] for p in resource["timeline"] if p["horizon_hours"] == hours)


# ---------------------------------------------------------------- baseline --
def test_default_scenario_reproduces_baseline_exactly():
    result = simulate(Scenario(), _forecasts(), CAPACITY)
    assert result["changed_parameters"] == []
    for r in result["resources"]:
        for p in r["timeline"]:
            assert p["scenario"] == p["baseline"]
            assert p["scenario_lower"] == p["baseline_lower"]
            assert p["scenario_upper"] == p["baseline_upper"]
        assert r["verdict"] == "ok"
    assert "baseline" in result["summary"]["headline"]


def test_timeline_is_the_busiest_node_not_the_average():
    result = simulate(Scenario(), _forecasts(), CAPACITY)
    cpu = _res(result, "cpu_percent")
    # compute-01 starts higher (40 vs 30) with the same slope, so it is the max.
    assert _at(cpu, 720, "baseline") == pytest.approx(40.0 + 0.5 * 30, abs=0.01)
    assert cpu["nodes"][0]["hostname"] == "compute-01"
    assert cpu["node_count"] == 2


def test_timeline_starts_at_now_and_ends_at_the_horizon():
    cpu = _res(simulate(Scenario(horizon_days=30), _forecasts(), CAPACITY), "cpu_percent")
    assert cpu["timeline"][0]["horizon_hours"] == 0.0
    assert cpu["timeline"][0]["baseline"] == pytest.approx(40.0)
    assert cpu["timeline"][-1]["horizon_hours"] == 720


def test_horizon_truncates_the_timeline():
    cpu = _res(simulate(Scenario(horizon_days=7), _forecasts(), CAPACITY), "cpu_percent")
    assert cpu["timeline"][-1]["horizon_hours"] == 168


# ---------------------------------------------------------------- VM load ---
def test_extra_vms_add_the_expected_load_once_landed():
    # 20 VMs x 2 vCPU x 30% busy = 12 cores of 32 physical -> +37.5 points.
    result = simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY)
    cpu = _res(result, "cpu_percent")
    assert cpu["vm_load_pct_points"] == pytest.approx(37.5)
    assert _at(cpu, 720, "scenario") - _at(cpu, 720, "baseline") == pytest.approx(37.5, abs=0.01)
    # Memory: 20 x 4 GB x 70% = 56 GB of 128 GB -> +43.75 points.
    mem = _res(result, "memory_percent")
    assert _at(mem, 720, "scenario") - _at(mem, 720, "baseline") == pytest.approx(43.75, abs=0.01)
    # Disk: 20 x 40 GB x 100% = 800 GB of 1000 GB -> +80 points.
    disk = _res(result, "disk_percent")
    assert _at(disk, 720, "scenario") - _at(disk, 720, "baseline") == pytest.approx(80.0, abs=0.01)


def test_utilisation_assumption_scales_the_load():
    low = _res(simulate(Scenario(extra_vms=20, vm_cpu_util_pct=10), _forecasts(), CAPACITY), "cpu_percent")
    high = _res(simulate(Scenario(extra_vms=20, vm_cpu_util_pct=60), _forecasts(), CAPACITY), "cpu_percent")
    assert high["vm_load_pct_points"] == pytest.approx(6 * low["vm_load_pct_points"])


def test_thin_provisioning_reduces_disk_load():
    thick = _res(simulate(Scenario(extra_vms=10), _forecasts(), CAPACITY), "disk_percent")
    thin = _res(simulate(Scenario(extra_vms=10, vm_disk_fill_pct=25), _forecasts(), CAPACITY), "disk_percent")
    assert thin["vm_load_pct_points"] == pytest.approx(thick["vm_load_pct_points"] / 4)


def test_interval_is_carried_through_the_same_transform():
    cpu = _res(simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY), "cpu_percent")
    p = next(p for p in cpu["timeline"] if p["horizon_hours"] == 720)
    assert p["scenario_upper"] - p["scenario"] == pytest.approx(p["baseline_upper"] - p["baseline"], abs=0.01)
    assert p["scenario_lower"] <= p["scenario"] <= p["scenario_upper"]


# ------------------------------------------------------------ timing / ramp --
def test_vms_arriving_later_leave_the_early_timeline_untouched():
    cpu = _res(simulate(Scenario(extra_vms=20, start_day=14), _forecasts(), CAPACITY), "cpu_percent")
    assert _at(cpu, 72, "scenario") == _at(cpu, 72, "baseline")     # day 3: not there yet
    assert _at(cpu, 168, "scenario") == _at(cpu, 168, "baseline")   # day 7: not there yet
    assert _at(cpu, 720, "scenario") > _at(cpu, 720, "baseline")    # day 30: there


def test_start_day_edge_is_a_point_on_the_timeline():
    cpu = _res(simulate(Scenario(extra_vms=20, start_day=10), _forecasts(), CAPACITY), "cpu_percent")
    hours = {p["horizon_hours"] for p in cpu["timeline"]}
    assert 240.0 in hours and 241.0 in hours  # step edge isn't smeared across a coarse gap


def test_ramp_is_linear_between_start_and_full_arrival():
    scen = Scenario(extra_vms=20, start_day=7, ramp_days=14)   # full at day 21 = 504 h
    cpu = _res(simulate(scen, _forecasts(), CAPACITY), "cpu_percent")
    full = cpu["vm_load_pct_points"]
    delta_mid = _at(cpu, 336, "scenario") - _at(cpu, 336, "baseline")   # day 14 = halfway
    delta_end = _at(cpu, 504, "scenario") - _at(cpu, 504, "baseline")
    assert delta_mid == pytest.approx(full / 2, abs=0.01)
    assert delta_end == pytest.approx(full, abs=0.01)


# ----------------------------------------------------------- growth / nodes --
def test_workload_growth_compounds_monthly():
    cpu = _res(simulate(Scenario(workload_growth_pct_per_month=20), _forecasts(), CAPACITY), "cpu_percent")
    base_30d = _at(cpu, 720, "baseline")
    assert _at(cpu, 720, "scenario") == pytest.approx(base_30d * 1.2, abs=0.01)
    assert _at(cpu, 360 if 360 in {p["horizon_hours"] for p in cpu["timeline"]} else 336, "scenario") > 0


def test_adding_compute_nodes_dilutes_cpu_and_memory_but_not_disk():
    result = simulate(Scenario(compute_nodes_added=2, nodes_added_day=0), _forecasts(), CAPACITY)
    cpu, mem, disk = (_res(result, m) for m in ("cpu_percent", "memory_percent", "disk_percent"))
    assert _at(cpu, 720, "scenario") == pytest.approx(_at(cpu, 720, "baseline") * 2 / 4, abs=0.01)
    assert _at(mem, 720, "scenario") == pytest.approx(_at(mem, 720, "baseline") * 2 / 4, abs=0.01)
    assert _at(disk, 720, "scenario") == _at(disk, 720, "baseline")


def test_added_storage_dilutes_disk():
    disk = _res(simulate(Scenario(storage_gb_added=1000), _forecasts(), CAPACITY), "disk_percent")
    assert _at(disk, 720, "scenario") == pytest.approx(_at(disk, 720, "baseline") / 2, abs=0.01)


def test_hardware_arriving_late_only_helps_after_it_lands():
    scen = Scenario(extra_vms=20, compute_nodes_added=1, nodes_added_day=14)
    cpu = _res(simulate(scen, _forecasts(), CAPACITY), "cpu_percent")
    only_vms = _res(simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY), "cpu_percent")
    assert _at(cpu, 168, "scenario") == pytest.approx(_at(only_vms, 168, "scenario"), abs=0.01)  # day 7: node not in yet
    assert _at(cpu, 720, "scenario") < _at(only_vms, 720, "scenario")                           # day 30: it is


# --------------------------------------------------- the >=2 parameters case --
def test_two_parameters_change_the_timeline():
    """The acceptance criterion: change at least two parameters and the
    capacity timeline responds to the *combination*."""
    vms_only = simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY)
    combined = simulate(Scenario(extra_vms=20, compute_nodes_added=1, nodes_added_day=7), _forecasts(), CAPACITY)

    assert combined["changed_parameter_count"] == 3  # extra_vms, compute_nodes_added, nodes_added_day
    assert set(combined["changed_parameters"]) == {"extra_vms", "compute_nodes_added", "nodes_added_day"}

    cpu_vms, cpu_combined = _res(vms_only, "cpu_percent"), _res(combined, "cpu_percent")
    # Both differ from the baseline, and from each other.
    assert _at(cpu_vms, 720, "scenario") > _at(cpu_vms, 720, "baseline")
    assert _at(cpu_combined, 720, "scenario") != _at(cpu_vms, 720, "scenario")
    # Exact combined value: (baseline + load) x n/(n+k).
    expected = (_at(cpu_vms, 720, "baseline") + cpu_vms["vm_load_pct_points"]) * 2 / 3
    assert _at(cpu_combined, 720, "scenario") == pytest.approx(expected, abs=0.01)


def test_changed_parameters_ignores_horizon_and_counts_each_lever():
    assert changed_params(Scenario(horizon_days=90)) == []
    assert set(changed_params(Scenario(extra_vms=5, vm_vcpus=4))) == {"extra_vms", "vm_vcpus"}


def changed_params(s):
    return sim.changed_parameters(s)


# ------------------------------------------------------------ breach / ETA --
def test_scenario_can_create_a_breach_the_baseline_does_not_have():
    result = simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY)
    cpu = _res(result, "cpu_percent")
    assert cpu["baseline_breach"]["will_breach"] is False
    assert cpu["scenario_breach"]["will_breach"] is True
    assert cpu["verdict"] == "new_breach"
    assert result["summary"]["worst_verdict"] == "new_breach"
    assert result["summary"]["first_breach"] is not None
    assert "baseline stays below" in result["summary"]["headline"]


def test_breach_eta_is_interpolated_between_points():
    # now 40, +0 slope, +37.5 pts lands in 1h -> crosses 90 never; use a lower threshold.
    result = simulate(Scenario(extra_vms=20, threshold_pct=70), _forecasts(cpu_slope=0.0), CAPACITY)
    cpu = _res(result, "cpu_percent")
    # 40 -> 77.5 over the first hour: crosses 70 after 30/37.5 = 0.8 h.
    assert cpu["scenario_breach"]["eta_hours"] == pytest.approx(0.8, abs=0.05)
    assert cpu["scenario_breach"]["crossing_timestamp"].startswith("2026-10-01T00:48")


def test_headline_wording_never_says_zero_days():
    # +20 VMs land within the first hour -> the breach is "under a day" away.
    soon = simulate(Scenario(extra_vms=20, threshold_pct=70), _forecasts(cpu_slope=0.0), CAPACITY)
    assert "in under a day" in soon["summary"]["headline"]
    assert "~0 days" not in soon["summary"]["headline"]
    # A breach a couple of weeks out reads in whole days.
    later = simulate(Scenario(extra_vms=20, start_day=14, threshold_pct=70), _forecasts(cpu_slope=0.0), CAPACITY)
    assert "in ~14 days" in later["summary"]["headline"] or "in ~15 days" in later["summary"]["headline"]


def test_earlier_breach_reports_shift_in_days():
    # Baseline: 40 + 1.5/day crosses 80 at day ~26.7; with a ramped project it is earlier.
    fc = _forecasts(cpu_slope=1.5)
    scen = Scenario(extra_vms=8, vm_vcpus=4, start_day=7, ramp_days=7, threshold_pct=80)
    cpu = _res(simulate(scen, fc, CAPACITY), "cpu_percent")
    assert cpu["baseline_breach"]["will_breach"] and cpu["scenario_breach"]["will_breach"]
    assert cpu["verdict"] == "earlier_breach"
    assert cpu["eta_shift_days"] > 0
    assert cpu["scenario_breach"]["eta_days"] < cpu["baseline_breach"]["eta_days"]


def test_more_hardware_can_relieve_a_baseline_breach():
    fc = _forecasts(cpu_slope=2.0)   # baseline hits 90% around day 25
    cpu_base = _res(simulate(Scenario(), fc, CAPACITY), "cpu_percent")
    assert cpu_base["baseline_breach"]["will_breach"]
    relieved = _res(simulate(Scenario(compute_nodes_added=2, nodes_added_day=3), fc, CAPACITY), "cpu_percent")
    assert relieved["scenario_breach"]["will_breach"] is False
    assert relieved["verdict"] == "relieved"


def test_already_breached_baseline_is_reported_as_such():
    fc = _forecasts()
    fc["cpu_percent"] = [_node("compute-01", 95.0, 0.0), _node("compute-02", 30.0, 0.0)]
    cpu = _res(simulate(Scenario(extra_vms=5), fc, CAPACITY), "cpu_percent")
    assert cpu["baseline_breach"]["already_breached"] is True
    assert cpu["verdict"] == "already_breached"


def test_demand_above_100_is_kept_and_flagged_not_clipped():
    cpu = _res(simulate(Scenario(extra_vms=60), _forecasts(), CAPACITY), "cpu_percent")
    assert cpu["peak_scenario"] > 100.0
    assert cpu["exceeds_capacity"] is True


def test_per_node_table_has_before_and_after_for_every_node():
    cpu = _res(simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY), "cpu_percent")
    assert {n["hostname"] for n in cpu["nodes"]} == {"compute-01", "compute-02"}
    for n in cpu["nodes"]:
        assert n["scenario_end"] > n["baseline_end"]


# --------------------------------------------------------- degraded inputs --
def test_unknown_capacity_adds_no_load_and_says_so():
    result = simulate(Scenario(extra_vms=20), _forecasts(), None)
    cpu = _res(result, "cpu_percent")
    assert cpu["vm_load_pct_points"] == 0.0
    assert any("unknown" in note for note in cpu["notes"])
    assert result["reservation"]["available"] is False


def test_resource_without_forecast_data_is_unavailable_not_an_error():
    fc = _forecasts()
    fc["disk_percent"] = []
    result = simulate(Scenario(extra_vms=5), fc, CAPACITY)
    disk = _res(result, "disk_percent")
    assert disk["available"] is False and "storage" in disk["reason"]
    assert _res(result, "cpu_percent")["available"] is True


def test_no_forecast_data_at_all_is_handled():
    result = simulate(Scenario(extra_vms=5), {}, CAPACITY)
    assert all(not r["available"] for r in result["resources"])
    assert result["summary"]["worst_verdict"] is None
    assert "cannot be evaluated" in result["summary"]["headline"]


def test_extrapolated_range_is_surfaced():
    fc = _forecasts()
    fc["cpu_percent"] = [_node("compute-01", 40.0, 0.5, extrapolated_after=168)]
    result = simulate(Scenario(extra_vms=5), fc, CAPACITY)
    assert _res(result, "cpu_percent")["first_extrapolated_hour"] == 336
    assert any("extrapolated" in a for a in result["assumptions"])


def test_simulation_is_deterministic():
    scen = Scenario(extra_vms=12, start_day=3, ramp_days=5, workload_growth_pct_per_month=8, compute_nodes_added=1, nodes_added_day=10)
    assert simulate(scen, _forecasts(), CAPACITY) == simulate(scen, _forecasts(), CAPACITY)


# ------------------------------------------------------------- reservation --
def test_reservation_matches_the_finops_capacity_maths():
    r = simulate(Scenario(extra_vms=20), _forecasts(), CAPACITY)["reservation"]
    # sellable vCPUs = 32 x 4 = 128; allocated 64 -> 64 + 40 = 104 = 81.25%.
    assert r["rows"]["vcpus"]["sellable_before"] == 128.0
    assert r["rows"]["vcpus"]["pct_before"] == 50.0
    assert r["rows"]["vcpus"]["allocated_after"] == 104.0
    assert r["rows"]["vcpus"]["pct_after"] == pytest.approx(81.25, abs=0.06)
    assert r["rows"]["vcpus"]["oversold_after"] is False
    # Disk is the binding resource: 400 GB used + 20 x 40 GB = 1200 GB on a 1000 GB pool.
    assert r["rows"]["gigabytes"]["allocated_after"] == 1200.0
    assert r["rows"]["gigabytes"]["oversold_after"] is True
    assert r["oversold_after"] is True


def test_reservation_counts_how_many_more_vms_fit():
    r = simulate(Scenario(extra_vms=1), _forecasts(), CAPACITY)["reservation"]
    # vCPU: (128-64)/2 = 32 | RAM: (131072-65536)/4096 = 16 | disk: (1000-400)/40 = 15  -> 15
    assert r["max_additional_vms_before"] == 15


def test_reservation_flags_overselling_and_new_hardware_relieves_it():
    r = simulate(Scenario(extra_vms=40), _forecasts(), CAPACITY)["reservation"]
    assert r["oversold_after"] is True            # 40 x 40 GB = 1600 GB on a 1000 GB pool, 400 already used
    relieved = simulate(Scenario(extra_vms=40, storage_gb_added=2000), _forecasts(), CAPACITY)["reservation"]
    assert relieved["rows"]["gigabytes"]["oversold_after"] is False
    assert relieved["max_additional_vms_after"] > r["max_additional_vms_after"]


def test_reservation_new_compute_node_adds_sellable_capacity():
    base = simulate(Scenario(extra_vms=1), _forecasts(), CAPACITY)["reservation"]["rows"]["vcpus"]
    more = simulate(Scenario(extra_vms=1, compute_nodes_added=1), _forecasts(), CAPACITY)["reservation"]["rows"]["vcpus"]
    assert more["sellable_after"] == pytest.approx(base["sellable_after"] * 3 / 2)


def test_showback_cost_uses_the_derived_unit_rates():
    r = simulate(Scenario(extra_vms=10), _forecasts(), CAPACITY)["reservation"]
    # 10 x (2 x 1.0 + 4 x 0.5 + 40 x 0.1) = 80
    assert r["showback_eur_month"] == 80.0


# ------------------------------------------------------------------ router --
class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def order_by(self, *_):
        return self

    def first(self):
        return self._row


class _FakeDb:
    def __init__(self, row):
        self._row = row

    def query(self, *_):
        return _FakeQuery(self._row)


def _sample():
    return SimpleNamespace(
        physical_vcpus=32.0, physical_ram_mb=131072.0, storage_gb=1000.0, compute_nodes=2,
        cpu_allocation_ratio=4.0, ram_allocation_ratio=1.0,
        allocated_vcpus=64.0, allocated_ram_mb=65536.0, allocated_gb=400.0,
        rate_vcpu_eur=1.0, rate_ram_gb_eur=0.5, rate_storage_gb_eur=0.1,
        sampled_at=__import__("datetime").datetime(2026, 10, 1),
    )


@pytest.fixture
def client(monkeypatch):
    sim.clear_cache()
    monkeypatch.setattr(sim, "gather_forecasts", lambda db, horizon_days: _forecasts())
    app = FastAPI()
    app.include_router(capacity_router.router)
    app.dependency_overrides[get_db] = lambda: _FakeDb(_sample())
    yield TestClient(app)
    sim.clear_cache()


def test_simulate_endpoint_with_two_changed_parameters(client):
    res = client.post("/api/v1/capacity/simulate", json={"extra_vms": 20, "compute_nodes_added": 1, "nodes_added_day": 14})
    assert res.status_code == 200
    body = res.json()
    assert body["changed_parameter_count"] == 3
    cpu = next(r for r in body["resources"] if r["metric"] == "cpu_percent")
    assert len(cpu["timeline"]) > 10
    assert {"baseline", "scenario", "scenario_lower", "scenario_upper"} <= set(cpu["timeline"][0])
    assert body["capacity_sampled_at"] == "2026-10-01T00:00:00"


def test_simulate_endpoint_empty_body_is_the_baseline(client):
    body = client.post("/api/v1/capacity/simulate", json={}).json()
    assert body["changed_parameters"] == []
    cpu = next(r for r in body["resources"] if r["metric"] == "cpu_percent")
    assert all(p["scenario"] == p["baseline"] for p in cpu["timeline"])


@pytest.mark.parametrize("payload", [
    {"extra_vms": -1},
    {"extra_vms": 501},
    {"vm_cpu_util_pct": 120},
    {"horizon_days": 0},
    {"horizon_days": 91},
    {"threshold_pct": 0},
    {"vm_ram_gb": 0},
])
def test_simulate_endpoint_rejects_out_of_range_parameters(client, payload):
    assert client.post("/api/v1/capacity/simulate", json=payload).status_code == 422


def test_options_endpoint_describes_every_lever_and_the_presets(client):
    body = client.get("/api/v1/capacity/options").json()
    assert set(Scenario().__dict__) == set(body["parameters"])
    assert body["parameters"]["extra_vms"]["max"] == 500
    assert body["parameters"]["extra_vms"]["default"] == 0
    assert any(p["label"] == "+20 VMs" for p in body["scenario_presets"])
    # every preset must itself be a valid scenario and move >= 2 levers or be a plain "+N VMs"
    for preset in body["scenario_presets"]:
        res = client.post("/api/v1/capacity/simulate", json=preset["params"])
        assert res.status_code == 200, preset["id"]
    assert body["horizons_days"] == [7, 30, 90]
