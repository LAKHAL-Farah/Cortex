"""A dead node must not open the breaker for healthy nodes (scope= keeps state per node)."""
from app.agents import resilience


def _boom():
    raise RuntimeError("down")


def test_scoped_breakers_are_isolated(monkeypatch):
    monkeypatch.setattr(resilience, "RETRY_PAUSE_SECONDS", 0)
    resilience.reset_all_breakers()
    bad = resilience.get_breaker("security.cve_feed", scope="storage", max_retries=0, failure_threshold=2)
    for _ in range(2):
        assert not bad.call(_boom).ok
    assert bad.state == "open"

    good = resilience.get_breaker("security.cve_feed", scope="compute1", max_retries=0, failure_threshold=2)
    assert good is not bad
    result = good.call(lambda: "ok")
    assert result.ok and result.value == "ok"


def test_scoped_breaker_keeps_source_name(monkeypatch):
    monkeypatch.setattr(resilience, "RETRY_PAUSE_SECONDS", 0)
    resilience.reset_all_breakers()
    b = resilience.get_breaker("security.ebpf", scope="n1", max_retries=0, failure_threshold=1)
    res = b.call(_boom)
    assert res.failure["source"] == "security.ebpf"


def test_unscoped_breaker_still_shared():
    resilience.reset_all_breakers()
    assert resilience.get_breaker("x.y") is resilience.get_breaker("x.y")
