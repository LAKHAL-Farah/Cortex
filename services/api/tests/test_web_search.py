"""Tests for services/web_search.py -- pure, no real network (requests.post
is monkeypatched), same pattern tests/test_security_agent.py uses for
cve_feed's HTTP client.
"""
import pytest

from app.services import web_search


def test_search_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(web_search, "TAVILY_API_KEY", None)
    with pytest.raises(web_search.WebSearchError):
        web_search.search("nova-compute down")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_search_returns_parsed_results(monkeypatch):
    monkeypatch.setattr(web_search, "TAVILY_API_KEY", "fake-key")
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({
            "results": [
                {
                    "title": "Bug 12345: nova-compute crashes on restart",
                    "url": "https://bugs.launchpad.net/nova/+bug/12345",
                    "content": "nova-compute crashes when librbd can't connect...",
                },
                {"title": "", "url": "https://ask.openstack.org/q/1", "content": "x" * 500},
            ]
        })

    monkeypatch.setattr(web_search.requests, "post", fake_post)

    results = web_search.search("nova-compute crash", max_results=5)

    assert len(results) == 2
    assert results[0].title == "Bug 12345: nova-compute crashes on restart"
    assert results[0].url == "https://bugs.launchpad.net/nova/+bug/12345"
    # blank title falls back to the URL
    assert results[1].title == results[1].url
    # snippet is truncated
    assert len(results[1].snippet) == web_search._MAX_SNIPPET_CHARS
    # query is biased with "OpenStack" and scoped to community domains
    assert captured["json"]["query"] == "OpenStack nova-compute crash"
    assert captured["json"]["include_domains"] == web_search.COMMUNITY_DOMAINS
    assert "docs.openstack.org" not in captured["json"]["include_domains"]


def test_search_skips_results_with_no_url(monkeypatch):
    monkeypatch.setattr(web_search, "TAVILY_API_KEY", "fake-key")
    monkeypatch.setattr(
        web_search.requests, "post",
        lambda *a, **k: _FakeResponse({"results": [{"title": "no url here", "content": "..."}]}),
    )
    assert web_search.search("anything") == []


def test_search_respects_max_results(monkeypatch):
    monkeypatch.setattr(web_search, "TAVILY_API_KEY", "fake-key")
    many = [{"title": f"t{i}", "url": f"https://bugs.launchpad.net/x/{i}", "content": ""} for i in range(10)]
    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: _FakeResponse({"results": many}))
    assert len(web_search.search("anything", max_results=3)) == 3


def test_search_wraps_request_exceptions(monkeypatch):
    monkeypatch.setattr(web_search, "TAVILY_API_KEY", "fake-key")

    def fake_post(*a, **k):
        raise web_search.requests.ConnectionError("timeout")

    monkeypatch.setattr(web_search.requests, "post", fake_post)
    with pytest.raises(web_search.WebSearchError):
        web_search.search("anything")


def test_search_wraps_non_2xx_responses(monkeypatch):
    monkeypatch.setattr(web_search, "TAVILY_API_KEY", "fake-key")

    class _BadResponse:
        def raise_for_status(self):
            raise web_search.requests.HTTPError("401 unauthorized")

    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: _BadResponse())
    with pytest.raises(web_search.WebSearchError):
        web_search.search("anything")
