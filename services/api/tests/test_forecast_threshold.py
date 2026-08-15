"""
services/api/tests/test_forecast_threshold.py

Unit tests for the threshold-breach ETA logic added for acceptance criterion
2.5 ("Warning 'X will hit threshold in ~N days'" / "Threshold breach ETA
shown on dashboard for at least one resource"), in
app.services.forecast_service: estimate_threshold_eta, get_threshold_warning,
list_threshold_warnings.

These build synthetic get_forecast()-shaped dicts directly rather than going
through the CSV dataset + trained models -- the ETA math is a pure function
of the already-served forecast trajectory, so it's tested in isolation from
forecasting itself (which has its own coverage need, tracked separately).
"""
from app.services import forecast_service as mod


def _forecast(metric, actual_value, forecast_points, generated_at="2026-08-15T00:00:00"):
    """Builds a minimal get_forecast()-shaped dict: just the fields
    estimate_threshold_eta actually reads."""
    return {
        "hostname": "host1",
        "metric": metric,
        "model_type": "ml_quantile",
        "generated_at": generated_at,
        "forecast": [
            {"horizon_hours": h, "timestamp": "irrelevant", "predicted": p, "lower": p - 5, "upper": p + 5}
            for h, p in forecast_points
        ],
        "actual": [{"timestamp": generated_at, "value": actual_value}],
    }


def test_no_breach_when_forecast_stays_below_threshold():
    fc = _forecast("cpu_percent", 40.0, [(1, 41.0), (24, 45.0), (168, 50.0)])
    warning = mod.estimate_threshold_eta(fc)
    assert warning["will_breach"] is False
    assert warning["already_breached"] is False
    assert warning["eta_hours"] is None
    assert warning["eta_days"] is None


def test_already_breached_reports_zero_eta():
    fc = _forecast("cpu_percent", 95.0, [(1, 95.0), (24, 96.0)])
    warning = mod.estimate_threshold_eta(fc)
    assert warning["will_breach"] is True
    assert warning["already_breached"] is True
    assert warning["eta_hours"] == 0.0
    assert warning["eta_days"] == 0.0


def test_interpolates_crossing_between_two_served_points():
    # Rises linearly from 70 at t=0 to 90 at t=24h and 100 at t=48h.
    # Threshold 90 should be hit right around the h=24 point.
    fc = _forecast("cpu_percent", 70.0, [(24, 90.0), (48, 100.0)])
    warning = mod.estimate_threshold_eta(fc)
    assert warning["will_breach"] is True
    assert warning["already_breached"] is False
    assert warning["eta_hours"] == 24.0
    assert warning["eta_days"] == 1.0
    assert warning["crossing_timestamp"] is not None


def test_interpolates_midway_between_points_not_just_nearest():
    # 60 -> 100 over a single 10h segment; crossing 80 should land halfway,
    # not snap to either endpoint.
    fc = _forecast("cpu_percent", 60.0, [(10, 100.0)])
    warning = mod.estimate_threshold_eta(fc, threshold=80.0)
    assert warning["eta_hours"] == 5.0
    assert warning["eta_days"] == round(5.0 / 24.0, 1)


def test_custom_threshold_overrides_default():
    fc = _forecast("cpu_percent", 50.0, [(1, 55.0), (24, 65.0)])
    # Default threshold (90) is never reached -> no breach.
    assert mod.estimate_threshold_eta(fc)["will_breach"] is False
    # A lower, explicit threshold is reached.
    warning = mod.estimate_threshold_eta(fc, threshold=60.0)
    assert warning["will_breach"] is True


def test_unknown_metric_without_default_threshold_returns_none():
    fc = _forecast("some_unmapped_metric", 10.0, [(1, 20.0)])
    assert mod.estimate_threshold_eta(fc) is None


def test_get_threshold_warning_returns_none_when_no_forecast(monkeypatch):
    monkeypatch.setattr(mod, "get_forecast", lambda hostname, metric: None)
    assert mod.get_threshold_warning("10.0.0.1", "cpu_percent") is None


def test_get_threshold_warning_attaches_hostname_and_model_type(monkeypatch):
    fc = _forecast("cpu_percent", 95.0, [(1, 96.0)])
    monkeypatch.setattr(mod, "get_forecast", lambda hostname, metric: fc)
    warning = mod.get_threshold_warning("10.0.0.1", "cpu_percent")
    assert warning["hostname"] == "10.0.0.1"
    assert warning["metric"] == "cpu_percent"
    assert warning["model_type"] == "ml_quantile"
    assert warning["will_breach"] is True


def test_list_threshold_warnings_filters_out_non_breaching_resources(monkeypatch):
    forecasts = {
        ("10.0.0.1", "cpu_percent"): _forecast("cpu_percent", 40.0, [(24, 45.0)]),  # safe
        ("10.0.0.1", "memory_percent"): _forecast("memory_percent", 92.0, [(1, 93.0)]),  # breach now
        ("10.0.0.2", "cpu_percent"): _forecast("cpu_percent", 70.0, [(48, 91.0)]),  # breach later
        ("10.0.0.2", "memory_percent"): _forecast("memory_percent", 30.0, [(24, 32.0)]),  # safe
    }

    def fake_get_forecast(hostname, metric_name):
        return forecasts.get((hostname, metric_name))

    monkeypatch.setattr(mod, "get_forecast", fake_get_forecast)

    nodes = [("web1", "10.0.0.1"), ("web2", "10.0.0.2")]
    result = mod.list_threshold_warnings(nodes, metric_names=["cpu_percent", "memory_percent"])

    # Only the two breaching resources are returned, soonest first, and the
    # logical hostname (not the dataset IP) is what's reported.
    assert [w["hostname"] for w in result] == ["web1", "web2"]
    assert [w["metric"] for w in result] == ["memory_percent", "cpu_percent"]
    assert result[0]["already_breached"] is True
    assert result[1]["eta_hours"] > 0


def test_list_threshold_warnings_empty_when_nothing_breaches(monkeypatch):
    fc = _forecast("cpu_percent", 10.0, [(24, 15.0)])
    monkeypatch.setattr(mod, "get_forecast", lambda hostname, metric: fc)
    result = mod.list_threshold_warnings([("web1", "10.0.0.1")], metric_names=["cpu_percent"])
    assert result == []
