import itertools
import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
HEADERS = {"X-API-Key": "test-key"}

# Unique (not fixed) per call, since the tests share one DB with no per-test
# rollback/cleanup -- a fixed default here collides with nodes created by
# earlier tests/runs and causes spurious 409s. A counter (rather than a
# random address) also rules out birthday-collision flakiness between calls
# within the same run.
_next_ip_octet = itertools.count(10)


def _payload(**overrides):
    base = {
        "hostname": f"test-node-{uuid.uuid4().hex[:6]}",
        "ip_address": f"10.0.1.{next(_next_ip_octet)}",
        "role": "compute",
        "exporter_port": 9100,
        "is_active": True,
    }
    base.update(overrides)
    return base


def test_create_list_get_node():
    payload = _payload()
    res = client.post("/api/v1/nodes", json=payload, headers=HEADERS)
    assert res.status_code == 201
    node_id = res.json()["id"]
    assert any(n["id"] == node_id for n in client.get("/api/v1/nodes").json())
    assert client.get(f"/api/v1/nodes/{node_id}").json()["role"] == "compute"


def test_update_node_role():
    node_id = client.post("/api/v1/nodes", json=_payload(), headers=HEADERS).json()["id"]
    res = client.put(f"/api/v1/nodes/{node_id}", json=_payload(role="storage"), headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["role"] == "storage"


def test_delete_node():
    node_id = client.post("/api/v1/nodes", json=_payload(), headers=HEADERS).json()["id"]
    assert client.delete(f"/api/v1/nodes/{node_id}", headers=HEADERS).status_code == 204
    assert client.get(f"/api/v1/nodes/{node_id}").status_code == 404


def test_accepts_storage_subnet_ip():
    # Real storage node lives on 10.0.2.0/24, not 10.0.1.0/24
    res = client.post("/api/v1/nodes", json=_payload(ip_address="10.0.2.3", role="storage"), headers=HEADERS)
    assert res.status_code == 201


def test_rejects_ip_outside_managed_subnets():
    assert client.post("/api/v1/nodes", json=_payload(ip_address="8.8.8.8"), headers=HEADERS).status_code == 422


def test_rejects_unknown_role():
    assert client.post("/api/v1/nodes", json=_payload(role="hypervisor-of-doom"), headers=HEADERS).status_code == 422


def test_rejects_duplicate_hostname():
    payload = _payload()
    client.post("/api/v1/nodes", json=payload, headers=HEADERS)
    dup = _payload(hostname=payload["hostname"])
    assert client.post("/api/v1/nodes", json=dup, headers=HEADERS).status_code == 409


def test_write_endpoints_require_api_key():
    assert client.post("/api/v1/nodes", json=_payload()).status_code in (401, 422)


def test_file_sd_reflects_active_nodes(tmp_path, monkeypatch):
    from app.services import prometheus_sd
    monkeypatch.setattr(prometheus_sd, "FILE_SD_PATH", str(tmp_path / "nodes.json"))
    # Unique hostname per run (see _payload's ip_address comment above) --
    # a fixed hostname would 409 against a node left over from a prior run
    # against the same DB, and the file never gets written at all.
    hostname = f"fsd-check-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/nodes", json=_payload(hostname=hostname), headers=HEADERS)
    import json
    data = json.loads((tmp_path / "nodes.json").read_text())
    assert any(t["labels"]["node"] == hostname for t in data)


def test_file_sd_file_is_world_readable(tmp_path, monkeypatch):
    # Prometheus runs as an unprivileged system user; the file the API
    # writes must not be mkstemp's default 0600.
    from app.services import prometheus_sd
    monkeypatch.setattr(prometheus_sd, "FILE_SD_PATH", str(tmp_path / "nodes.json"))
    hostname = f"perm-check-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/nodes", json=_payload(hostname=hostname), headers=HEADERS)
    mode = (tmp_path / "nodes.json").stat().st_mode & 0o777
    assert mode & 0o044 == 0o044  # owner+other read bits set