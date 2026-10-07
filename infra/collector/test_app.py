"""Run: cd infra/collector && pip install -r requirements.txt httpx pytest && pytest -q"""
import os
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ["COLLECTOR_FALCO_TOKEN"] = "s3cret"
os.environ["COLLECTOR_KEYSTONE_IGNORE_SOURCE_IPS"] = "10.0.1.4"
os.environ["COLLECTOR_DISABLE_BACKGROUND"] = "1"

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
    collector._store.clear()
    collector._alerts.clear()
    monkeypatch.setattr(collector, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(collector, "_ALERTS_FILE", str(tmp_path / "alerts.json"))
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


COMBINED_PKGS = DPKG + "@@UPGRADABLE@@" + UPGRADABLE
COMBINED_CHLOG = "@@CHANGELOG sudo@@\n" + CHANGELOG
COMBINED_PORTS = SS + "@@SENSOR@@active\n"


def full_runner():
    return make_runner({"dpkg-query": (0, COMBINED_PKGS), "apt-get changelog": (0, COMBINED_CHLOG),
                        "ss -H": (0, COMBINED_PORTS)})


def test_packages_endpoint_flags_security_updates(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", full_runner())
    collector.refresh_host("compute1")
    data = client.get("/packages", params={"host": "compute1"}).json()
    sudo = next(p for p in data if p["name"] == "sudo")
    assert sudo["security_update"] == {"version": "1.9.9-1ubuntu2.5", "cves": ["CVE-2025-1111", "CVE-2025-2222"]}
    assert "security_update" not in next(p for p in data if p["name"] == "curl")


def test_requests_never_touch_ssh_and_cold_start_is_503(monkeypatch):
    runner = full_runner()
    monkeypatch.setattr(collector, "RUNNER", runner)
    assert client.get("/packages", params={"host": "compute1"}).status_code == 503  # not collected yet
    assert runner.calls == []  # the request itself did no SSH
    collector.refresh_host("compute1")
    n = len(runner.calls)
    for _ in range(5):
        assert client.get("/packages", params={"host": "compute1"}).status_code == 200
        assert client.get("/listening-ports", params={"host": "compute1"}).status_code == 200
    assert len(runner.calls) == n  # served from the store, instantly


def test_failed_refresh_keeps_serving_old_data_until_max_stale(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", full_runner())
    collector.refresh_host("compute1")
    monkeypatch.setattr(collector, "RUNNER", make_runner({"dpkg-query": HTTPException(503, "ssh down"),
                                                         "ss -H": HTTPException(503, "ssh down")}))
    result = collector.refresh_host("compute1")
    assert result["packages"].startswith("failed") and result["ports"].startswith("failed")
    assert client.get("/packages", params={"host": "compute1"}).status_code == 200  # stale but present
    monkeypatch.setattr(collector, "MAX_STALE_SECONDS", -1)
    assert client.get("/packages", params={"host": "compute1"}).status_code == 503  # too old to trust


def test_split_changelogs():
    parts = collector.split_changelogs("@@CHANGELOG a@@\nx\n@@CHANGELOG b@@\ny\n")
    assert parts == {"a": "x\n", "b": "y\n"}


def test_unknown_host_is_404_and_ssh_failure_is_503(monkeypatch):
    assert client.get("/packages", params={"host": "nope"}).status_code == 404
    monkeypatch.setattr(collector, "RUNNER", make_runner({"ss -H": HTTPException(503, "ssh down")}))
    assert collector.refresh_host("compute1")["ports"].startswith("failed")
    assert client.get("/listening-ports", params={"host": "compute1"}).status_code == 503


def test_ports_endpoint(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", full_runner())
    collector.refresh_host("compute1")
    assert len(client.get("/listening-ports", params={"host": "compute1"}).json()) == 4


def test_falco_ingest_auth_filter_and_alerts(monkeypatch):
    monkeypatch.setattr(collector, "RUNNER", full_runner())
    collector.refresh_host("compute1")
    collector.store_put(("sensor", "controller"), True)
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
    monkeypatch.setattr(collector, "RUNNER", make_runner({"ss -H": (0, SS + "@@SENSOR@@inactive\n")}))
    collector.refresh_host("compute1")
    assert client.get("/alerts", params={"host": "compute1"}).status_code == 503


def test_alert_ttl(monkeypatch):
    collector.store_put(("sensor", "compute1"), True)
    collector._alerts.append((time.time() - 99999, {"host": "compute1", "rule": "old", "priority": "critical",
                                                    "output": "", "time": ""}))
    assert client.get("/alerts", params={"host": "compute1"}).json() == []


def test_keystone_log_parsing_and_endpoint(monkeypatch):
    events = collector.parse_keystone_access_log(ACCESS, ignore_ips={"10.0.1.4"})
    assert len(events) == 3 and {e["source_ip"] for e in events} == {"10.0.1.9"}  # 401, GET and ignored IP dropped
    assert events[0]["issued_at"] == "2026-10-05T19:31:01Z" and events[0]["expires_at"] == "2026-10-05T20:31:01Z"
    monkeypatch.setattr(collector, "RUNNER", make_runner({"docker exec keystone tail": (0, ACCESS)}))
    assert len(client.get("/_sandbox/keystone/token-log").json()) == 3
    collector._store.clear()
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


def test_ignore_regex_and_alert_persistence(monkeypatch, tmp_path):
    monkeypatch.setattr(collector, "ALERT_IGNORE_REGEX", r"file=/etc/pam\.d/")
    collector.store_put(("sensor", "compute1"), True)
    pam = {"hostname": "compute1", "rule": "Read sensitive file untrusted", "priority": "Warning",
           "output": "x file=/etc/pam.d/common-auth process=sshd"}
    shadow = {"hostname": "compute1", "rule": "Read sensitive file untrusted", "priority": "Warning",
              "output": "x file=/etc/shadow process=cat"}
    assert client.post("/falco?token=s3cret", json=[pam, shadow]).json()["stored"] == 1
    assert (tmp_path / "alerts.json").exists()
    collector._alerts.clear()
    collector._load_alerts()  # what a restart does
    assert [a["output"] for a in client.get("/alerts", params={"host": "compute1"}).json()] == [shadow["output"]]


# ------------------------------------------------------------------ Trivy
import base64  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import trivy_aggregate  # noqa: E402


def _v(cve, pkg, sev, installed="1.0", fixed=None):
    out = {"VulnerabilityID": cve, "PkgName": pkg, "InstalledVersion": installed, "Severity": sev}
    if fixed:
        out["FixedVersion"] = fixed
    return out


SCAN = {"Results": [{"Vulnerabilities": [
    # old kernel, fixed in newer kernels: counted once per CVE even though 3 packages carry it
    _v("CVE-K1", "linux-headers-6.8.0-40-generic", "CRITICAL", "6.8.0-40.40~22.04.3", "6.8.0-50"),
    _v("CVE-K1", "linux-modules-6.8.0-40-generic", "CRITICAL", "6.8.0-40.40~22.04.3", "6.8.0-50"),
    _v("CVE-K1", "linux-hwe-6.8-tools-6.8.0-40", "CRITICAL", "6.8.0-40.40~22.04.3", "6.8.0-50"),
    _v("CVE-K2", "linux-headers-6.8.0-40-generic", "HIGH", "6.8.0-40.40~22.04.3", "6.8.0-100"),
    # running kernel: unfixed CVEs are ignored, a fixed one is kept
    _v("CVE-K3", "linux-image-6.8.0-138-generic", "HIGH", "6.8.0-138.138~22.04.1"),
    _v("CVE-K4", "linux-image-6.8.0-138-generic", "MEDIUM", "6.8.0-138.138~22.04.1", "6.8.0-150"),
    # user-space packages, unfixed kept
    _v("CVE-F1", "libavcodec58", "MEDIUM", "7:4.4.2"), _v("CVE-F2", "libavcodec58", "MEDIUM", "7:4.4.2"),
    _v("CVE-F3", "libavcodec58", "LOW", "7:4.4.2"),  # below the MEDIUM floor
    _v("CVE-G1", "libgstreamer-plugins-bad1.0-0", "HIGH", "1.20", "1.20.3"),
]}]}


def test_aggregate_collapses_kernel_noise_and_keeps_userspace_rows():
    out = trivy_aggregate.aggregate(SCAN, "6.8.0-138-generic", "MEDIUM")
    assert out["total_findings"] == 10 and out["running_kernel"] == "6.8.0-138"
    names = [p["name"] for p in out["packages"]]
    assert names == ["libgstreamer-plugins-bad1.0-0", "libavcodec58"]  # worst severity first
    ff = out["packages"][1]
    assert ff["count"] == 2 and ff["fixable"] == 0 and ff["by_severity"] == {"MEDIUM": 2}  # LOW dropped
    kernels = {k["release"]: k for k in out["kernels"]}
    assert kernels["6.8.0-40"]["count"] == 2 and kernels["6.8.0-40"]["running"] is False  # K1 once, K2
    assert kernels["6.8.0-138"]["count"] == 1 and kernels["6.8.0-138"]["running"] is True  # only K4 (K3 unfixed)
    assert out["kernels"][0]["running"] is True  # running kernel listed first


def test_aggregate_script_runs_standalone_like_it_does_on_the_node(tmp_path):
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps(SCAN))
    done = subprocess.run([sys.executable, trivy_aggregate.__file__, str(scan), "MEDIUM", "6.8.0-138-generic"],
                          capture_output=True, text=True, check=True)
    assert json.loads(done.stdout.strip().splitlines()[-1])["packages"][0]["name"] == "libgstreamer-plugins-bad1.0-0"


def test_trivy_command_uses_host_network_and_ships_the_aggregator():
    cmd = collector._trivy_command("MEDIUM")
    assert "--network host" in cmd and "--severity CRITICAL,HIGH,MEDIUM" in cmd and "--cpus 1" in cmd
    payload = cmd.split("echo ")[1].split(" | base64 -d")[0]
    assert base64.b64decode(payload).decode() == open(trivy_aggregate.__file__).read()


def test_trivy_refresh_merge_status_and_failure_keeps_old(monkeypatch):
    aggregated = json.dumps(trivy_aggregate.aggregate(SCAN, "6.8.0-138-generic", "MEDIUM"))
    monkeypatch.setattr(collector, "LONG_RUNNER", make_runner({"docker run": (0, "warning noise\n" + aggregated + "\n")}))
    monkeypatch.setattr(collector, "RUNNER", full_runner())
    collector.refresh_host("compute1")
    assert "package rows" in collector.refresh_trivy_host("compute1")

    data = client.get("/packages", params={"host": "compute1"}).json()
    by_name = {p["name"]: p for p in data}
    assert "vulnerabilities" not in by_name["curl"]                      # untouched
    assert "linux-kernel-6.8.0-40" in by_name and by_name["linux-kernel-6.8.0-40"]["vulnerabilities"]["kernel"] is True
    assert by_name["linux-kernel-6.8.0-40"]["vulnerabilities"]["running"] is False
    status = client.get("/trivy/status").json()
    assert status["compute1"]["raw_findings"] == 10 and status["controller"] == "never scanned"

    monkeypatch.setattr(collector, "LONG_RUNNER", make_runner({"docker run": (1, "boom")}))
    with pytest.raises(HTTPException):
        collector.refresh_trivy_host("compute1")
    assert "linux-kernel-6.8.0-40" in {p["name"] for p in client.get("/packages", params={"host": "compute1"}).json()}


def test_trivy_result_survives_a_restart(monkeypatch):
    aggregated = json.dumps(trivy_aggregate.aggregate(SCAN, "6.8.0-138-generic", "MEDIUM"))
    monkeypatch.setattr(collector, "LONG_RUNNER", make_runner({"docker run": (0, aggregated)}))
    collector.refresh_trivy_host("storage")
    collector._store.clear()
    collector._load_trivy()
    assert collector.store_get(("trivy", "storage"))["result"]["total_findings"] == 10
