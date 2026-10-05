"""Run: cd infra/collector && pip install -r requirements.txt httpx pytest && pytest -q"""
import os
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ["COLLECTOR_FALCO_TOKEN"] = "s3cret"
os.environ["COLLECTOR_KEYSTONE_IGNORE_SOURCE_IPS"] = "10.0.1.4"

import collector  # noqa: E402

DPKG = "openssh-server\t1:8.9p1-3ubuntu0.10\nsudo\t1.9.9-1ubuntu2.4\ncurl\t7.81.0-1ubuntu1.18\n"
UPGRADABLE = (
    "Listing...\n"
    "sudo/jammy-updates,jammy-security 1.9.9-1ubuntu2.5 amd64 [upgradable from: 1.9.9-1ubuntu2.4]\n"
    "vim/jammy-updates 2:8.2.3995-1ubuntu2.20 amd64 [upgradable from: 2:8.2.3995-1ubuntu2.19]\n"
)
CHANGELOG = (
    "sudo (1.9.9-1ubuntu2.5) jammy-security; urgency=medium\n\n"
    "  * SECURITY UPDATE: Fix something\n    - debian/patches/CVE-2025-1111.patch\n    - CVE-2025-1111\n"
    "  * SECURITY UPDATE: Another\n    - CVE-2025-2222\n\n"
    " -- Someone <a@b.c>  Mon, 01 Jan 2025 00:00:00 +0000\n\n"
    "sudo (1.9.9-1ubuntu2.4) jammy-security; urgency=medium\n\n"
    "  * SECURITY UPDATE: old fix\n    - CVE-2023-9999\n"
)
SS = (
    'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
    'tcp LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=1,fd=4))\n'
    'tcp LISTEN 0 4096 127.0.0.1:3306 0.0.0.0:* users:(("mariadbd",pid=2,fd=5))\n'
    'tcp LISTEN 0 511 10.0.1.5:5000 0.0.0.0:* users:(("haproxy",pid=3,fd=6))\n'
    'udp UNCONN 0 0 0.0.0.0:123 0.0.0.0:* users:(("chronyd",pid=4,fd=7))\n'
    'tcp LISTEN 0 10 *:9100 *:*\n'
)
ACCESS = (
    '10.0.1.9 - - [05/Oct/2026:19:31:01 +0000] "POST /v3/auth/tokens HTTP/1.1" 201 2345 "-" "curl/7"\n'
    '10.0.1.9 - - [05/Oct/2026:19:31:02 +0000] "POST /v3/auth/tokens HTTP/1.1" 201 2345 "-" "curl/7"\n'
    '10.0.1.9 - - [05/Oct/2026:19:31:03 +0000] "POST /v3/auth/tokens HTTP/1.1" 201 2345 "-" "curl/7"\n'
    '10.0.1.4 - - [05/Oct/2026:19:31:04 +0000] "POST /v3/auth/tokens HTTP/1.1" 201 2345 "-" "keystoneauth"\n'
    '172.16.0.8 - - [05/Oct/2026:19:31:05 +0000] "POST /v3/auth/tokens HTTP/1.1" 401 99 "-" "curl/7"\n'
    '10.0.1.9 - - [05/Oct/2026:19:31:06 +0000] "GET /v3/projects HTTP/1.1" 200 99 "-" "curl/7"\n'
)


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    collector._cache.clear()
    collector._alerts.clear()
    inv = tmp_path / "hosts.ini"
    inv.write_text("[controllers]\ncontroller ansible_host=10.0.1.4 ansible_user=root node_role=controller\n"
                   "[computes]\ncompute1 ansible_host=10.0.1.6 ansible_user=root node_role=compute\n")
    monkeypatch.setattr(collector, "INVENTORY_PATH", str(inv))


def make_runner(responses):
    calls = []

    def run(host, cmd):
        calls.append((host, cmd))
        for needle, result in responses.items():
            if needle in cmd:
                if isinstance(result, Exception):
                    raise result
                return result
        return 1, ""
    run.calls = calls
    return run


client = TestClient(collector.app)


def test_parsers():
    assert {"name": "sudo", "version": "1.9.9-1ubuntu2.4"} in collector.parse_dpkg(DPKG)
    assert list(collector.parse_security_upgradable(UPGRADABLE)) == ["sudo"]  # vim is not a security pocket
    assert collector.parse_changelog_cves(CHANGELOG, "1.9.9-1ubuntu2.4") == ["CVE-2025-1111", "CVE-2025-2222"]
    ports = collector.parse_ss(SS)
    assert [(p["port"], p["protocol"]) for p in ports] == [(22, "tcp"), (123, "udp"), (5000, "tcp"), (9100, "tcp")]
    assert next(p for p in ports if p["port"] == 22)["process"] == "sshd"  # v4/v6 merged
    assert all(p["port"] != 3306 for p in ports)  # loopback-only dropped


def test_packages_endpoint_flags_security_updates(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", make_runner({
        "dpkg-query": (0, DPKG), "apt list --upgradable": (0, UPGRADABLE), "apt-get changelog sudo": (0, CHANGELOG)}))
    data = client.get("/packages", params={"host": "compute1"}).json()
    sudo = next(p for p in data if p["name"] == "sudo")
    assert sudo["security_update"] == {"version": "1.9.9-1ubuntu2.5", "cves": ["CVE-2025-1111", "CVE-2025-2222"]}
    assert "security_update" not in next(p for p in data if p["name"] == "curl")


def test_unknown_host_is_404_and_ssh_failure_is_503(monkeypatch):
    assert client.get("/packages", params={"host": "nope"}).status_code == 404
    monkeypatch.setattr(collector, "RUNNER", make_runner({"ss -H": HTTPException(503, "ssh down")}))
    assert client.get("/listening-ports", params={"host": "compute1"}).status_code == 503


def test_ports_endpoint(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", make_runner({"ss -H": (0, SS)}))
    assert len(client.get("/listening-ports", params={"host": "compute1"}).json()) == 4


def test_falco_ingest_auth_filter_and_alerts(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", make_runner({"systemctl is-active": (0, "active\n")}))
    ev = {"hostname": "compute1", "rule": "Terminal shell in container", "priority": "Warning",
          "output": "shell spawned", "time": "2026-10-05T19:00:00Z"}
    low = {"hostname": "compute1", "rule": "Noise", "priority": "Notice", "output": "x"}
    assert client.post("/falco", json=ev).status_code == 401
    assert client.post("/falco?token=wrong", json=ev).status_code == 401
    r = client.post("/falco?token=s3cret", json=[ev, low]).json()
    assert r == {"received": 2, "stored": 1}  # Notice is below the default minimum
    got = client.get("/alerts", params={"host": "compute1"}).json()
    assert [a["rule"] for a in got] == ["Terminal shell in container"]
    assert client.get("/alerts", params={"host": "controller"}).json() == []


def test_alerts_503_when_sensor_not_running(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", make_runner({"systemctl is-active": (3, "inactive\n")}))
    assert client.get("/alerts", params={"host": "compute1"}).status_code == 503


def test_alert_ttl(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", make_runner({"systemctl is-active": (0, "active\n")}))
    collector._alerts.append((time.time() - 99999, {"host": "compute1", "rule": "old", "priority": "critical",
                                                    "output": "", "time": ""}))
    assert client.get("/alerts", params={"host": "compute1"}).json() == []


def test_keystone_log_parsing_and_endpoint(monkeypatch):
    events = collector.parse_keystone_access_log(ACCESS, ignore_ips={"10.0.1.4"})
    assert len(events) == 3 and {e["source_ip"] for e in events} == {"10.0.1.9"}  # 401, GET and ignored IP dropped
    assert events[0]["issued_at"] == "2026-10-05T19:31:01Z" and events[0]["expires_at"] == "2026-10-05T20:31:01Z"
    monkeypatch.setattr(collector, "RUNNER", make_runner({"docker exec keystone tail": (0, ACCESS)}))
    assert len(client.get("/_sandbox/keystone/token-log").json()) == 3
    collector._cache.clear()
    monkeypatch.setattr(collector, "RUNNER", make_runner({"docker exec keystone tail": (1, "")}))
    assert client.get("/_sandbox/keystone/token-log").status_code == 503


def test_output_feeds_the_unmodified_api_keystone_rule():
    """The log-derived events must trigger the API's rapid-reissue rule."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "api"))
    from datetime import datetime, timezone
    from app.services import keystone_audit
    events = collector.parse_keystone_access_log(ACCESS, ignore_ips={"10.0.1.4"})
    result = keystone_audit.find_abusive_token_patterns(events, now=datetime(2026, 10, 5, 19, 32, tzinfo=timezone.utc))
    assert result["rapid_reissue"] and result["rapid_reissue"][0]["count"] == 3
