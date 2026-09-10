import pytest

from app.agents.resilience import reset_all_breakers


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Circuit breakers (app/agents/resilience.py) are deliberately
    process-global -- that's what lets one actually open after repeated
    failures instead of resetting every call. That same persistence would
    let one test's induced failures leak into the next test's (e.g. a test
    that trips anomaly.loki's failure_threshold would leave the breaker
    open for `reset_after_seconds`, silently short-circuiting a later
    test's "Loki call succeeds" case with a stale "circuit_open" failure).
    Reset before every test so each one starts from a clean, closed state.
    """
    reset_all_breakers()
    yield
    reset_all_breakers()
