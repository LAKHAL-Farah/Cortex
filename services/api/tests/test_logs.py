from fastapi.testclient import TestClient
from app.main import app
from app.services import loki_client

client = TestClient(app)


FAKE_STREAMS = [
    {
        "stream": {"host": "compute1-sim", "role": "compute", "job": "nova", "service": "nova"},
        "values": [
            ["1700000002000000000", "2023-11-14T12:00:02.000Z INFO [nova] compute1-sim req_id=aaa - handling incoming request"],
            ["1700000001000000000", "2023-11-14T12:00:01.000Z ERROR [nova] compute1-sim req_id=bbb - retrying operation after transient error"],
        ],
    },
    {
        "stream": {"host": "controller-sim", "role": "controller"},
        "values": [
            ["1700000000000000000", "Nov 14 12:00:00 controller-sim systemd[1]: Started some.service"],
        ],
    },
]


def test_get_logs_returns_entries_newest_first(monkeypatch):
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: FAKE_STREAMS)
    res = client.get("/api/v1/logs")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 3
    # newest (highest ts) first
    assert body[0]["ts"] >= body[1]["ts"] >= body[2]["ts"]
    assert body[0]["host"] == "compute1-sim"
    assert body[0]["source"] == "nova"


def test_get_logs_builds_host_selector(monkeypatch):
    captured = {}

    def fake_query_range(logql, start, end, limit=500, direction="backward"):
        captured["logql"] = logql
        return []

    monkeypatch.setattr(loki_client, "query_range", fake_query_range)
    res = client.get("/api/v1/logs", params={"host": "compute1-sim", "source": "nova"})
    assert res.status_code == 200
    assert 'host="compute1-sim"' in captured["logql"]
    assert 'job="nova"' in captured["logql"]


def test_get_logs_level_filter_is_line_filter(monkeypatch):
    captured = {}

    def fake_query_range(logql, start, end, limit=500, direction="backward"):
        captured["logql"] = logql
        return []

    monkeypatch.setattr(loki_client, "query_range", fake_query_range)
    res = client.get("/api/v1/logs", params={"level": "error"})
    assert res.status_code == 200
    assert '|= "ERROR"' in captured["logql"]


def test_get_logs_search_query_is_escaped(monkeypatch):
    captured = {}

    def fake_query_range(logql, start, end, limit=500, direction="backward"):
        captured["logql"] = logql
        return []

    monkeypatch.setattr(loki_client, "query_range", fake_query_range)
    res = client.get("/api/v1/logs", params={"q": 'say "hi"'})
    assert res.status_code == 200
    assert '|= "say \\"hi\\""' in captured["logql"]


def test_get_logs_propagates_loki_failure(monkeypatch):
    def boom(*a, **k):
        raise Exception("connection refused")

    monkeypatch.setattr(loki_client, "query_range", boom)
    res = client.get("/api/v1/logs")
    assert res.status_code == 502


def test_get_hosts(monkeypatch):
    monkeypatch.setattr(loki_client, "label_values", lambda label: ["compute1-sim", "controller-sim"])
    res = client.get("/api/v1/logs/hosts")
    assert res.status_code == 200
    assert res.json() == ["compute1-sim", "controller-sim"]


def test_get_sources(monkeypatch):
    monkeypatch.setattr(loki_client, "label_values", lambda label: ["system", "nova", "cinder"])
    res = client.get("/api/v1/logs/sources")
    assert res.status_code == 200
    assert res.json() == ["cinder", "nova", "system"]
