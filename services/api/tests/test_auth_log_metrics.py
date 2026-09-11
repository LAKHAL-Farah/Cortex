from app.services import auth_log_metrics


def test_failed_login_stats_counts_lines_and_extracts_source_ips(monkeypatch):
    calls = []

    def fake_query_range(logql, start, end, limit=500, direction="backward"):
        calls.append((logql, start, end, limit, direction))
        return [
            {
                "stream": {"host": "compute1"},
                "values": [
                    ["1", "sshd: Failed password for root from 10.0.0.2 port 22 ssh2"],
                    ["2", "sshd: Failed password for root from 10.0.0.2 port 22 ssh2"],
                ],
            },
            {"stream": {"host": "compute1"}, "values": [["3", "Failed password without source"]]},
        ]

    monkeypatch.setattr(auth_log_metrics.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(auth_log_metrics.loki_client, "query_range", fake_query_range)

    stats = auth_log_metrics.get_failed_login_stats("compute1")

    assert stats.count == 3
    assert stats.source_ips == ["10.0.0.2"]
    assert calls == [('{host="compute1"} |= "Failed password"', 700.0, 1_000.0, 500, "backward")]


def test_successful_login_stats_escapes_hostname_and_returns_none_without_ips(monkeypatch):
    captured = {}

    def fake_query_range(logql, start, end, **kwargs):
        captured["query"] = logql
        return [{"values": [["1", "Accepted publickey for root"]]}]

    monkeypatch.setattr(auth_log_metrics.time, "time", lambda: 2_000.0)
    monkeypatch.setattr(auth_log_metrics.loki_client, "query_range", fake_query_range)

    stats = auth_log_metrics.get_successful_login_stats('node"one')

    assert captured["query"] == '{host="node\\"one"} |= "Accepted "'
    assert stats == auth_log_metrics.Stats(count=1, source_ips=None)