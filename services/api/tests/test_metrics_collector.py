"""
services/api/tests/test_metrics_collector.py

Regression tests for the time-range bug on the node detail page: get_history()
used to hardcode step="15s" regardless of the requested range. For longer
windows (6h/24h/7d) that produces far more points than Prometheus's
query_range endpoint allows per series, so the query failed, the failure was
swallowed, and the UI silently fell back to a flat 2-point line -- making it
look like switching time ranges did nothing.
"""
import app.services.metrics_collector as mod
from app.services.metrics_collector import _step_for_range, get_history


def test_step_scales_up_for_longer_ranges():
    # Longer ranges need a coarser step to stay well under Prometheus's
    # point-count limit; each range should be no finer than the last.
    steps = [int(_step_for_range(m).rstrip("s")) for m in (15, 60, 360, 1440, 10080)]
    assert steps == sorted(steps)


def test_step_never_goes_below_15s_floor():
    assert _step_for_range(1) == "15s"


def test_step_keeps_point_count_bounded_for_a_week():
    # 7 days at the chosen step should stay comfortably under a limit
    # Prometheus would actually reject (its own default cap is ~11,000).
    step_seconds = int(_step_for_range(10080).rstrip("s"))
    point_count = (10080 * 60) / step_seconds
    assert point_count < 2000


def test_get_history_passes_the_scaled_step_to_query_range(monkeypatch):
    seen_steps = []

    def fake_query_range(promql, start, end, step):
        seen_steps.append(step)
        return []

    monkeypatch.setattr(mod, "query_range", fake_query_range)
    get_history("10.0.1.2:9100", minutes=10080)

    # One call per metric in METRIC_QUERIES, all using the range-appropriate
    # step rather than the old hardcoded "15s".
    assert seen_steps
    assert all(s == _step_for_range(10080) for s in seen_steps)
    assert seen_steps[0] != "15s"


def test_get_history_still_honors_an_explicit_step_override(monkeypatch):
    seen_steps = []

    def fake_query_range(promql, start, end, step):
        seen_steps.append(step)
        return []

    monkeypatch.setattr(mod, "query_range", fake_query_range)
    get_history("10.0.1.2:9100", minutes=60, step="30s")

    assert all(s == "30s" for s in seen_steps)
