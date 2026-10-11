import uuid
from fastapi.testclient import TestClient
from app.main import app
from tests.node_ips import next_free_node_ip

client = TestClient(app)

# Unique (not fixed) per call, since the tests share one DB with no per-test
# rollback/cleanup -- a fixed default here collides with nodes created by
# earlier tests/runs and causes spurious 409s. The address is picked by asking
# the DB which ones are taken (see tests/node_ips.py) rather than from a local
# counter, because other test modules register nodes in the same subnet too.


def _payload(**overrides):
    base = {
        "hostname": f"test-node-{uuid.uuid4().hex[:6]}",
        "ip_address": next_free_node_ip(),
        "role": "compute",
        "exporter_port": 9100,
        "is_active": True,
    }
    base.update(overrides)
    return base


def test_create_list_get_node(admin_headers):
    payload = _payload()
    res = client.post("/api/v1/nodes", json=payload, headers=admin_headers)
    assert res.status_code == 201
    node_id = res.json()["id"]
    assert any(n["id"] == node_id for n in client.get("/api/v1/nodes", headers=admin_headers).json())
    assert client.get(f"/api/v1/nodes/{node_id}", headers=admin_headers).json()["role"] == "compute"


def test_update_node_role(admin_headers):
    node_id = client.post("/api/v1/nodes", json=_payload(), headers=admin_headers).json()["id"]
    res = client.put(f"/api/v1/nodes/{node_id}", json=_payload(role="storage"), headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["role"] == "storage"


def test_delete_node(admin_headers):
    node_id = client.post("/api/v1/nodes", json=_payload(), headers=admin_headers).json()["id"]
    assert client.delete(f"/api/v1/nodes/{node_id}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/v1/nodes/{node_id}", headers=admin_headers).status_code == 404


def test_accepts_storage_subnet_ip(admin_headers):
    # Real storage node lives on 10.0.2.0/24, not 10.0.1.0/24
    res = client.post("/api/v1/nodes", json=_payload(ip_address="10.0.2.3", role="storage"), headers=admin_headers)
    assert res.status_code == 201


def test_rejects_ip_outside_managed_subnets(admin_headers):
    assert client.post("/api/v1/nodes", json=_payload(ip_address="8.8.8.8"), headers=admin_headers).status_code == 422


def test_rejects_unknown_role(admin_headers):
    assert client.post("/api/v1/nodes", json=_payload(role="hypervisor-of-doom"), headers=admin_headers).status_code == 422


def test_rejects_duplicate_hostname(admin_headers):
    payload = _payload()
    client.post("/api/v1/nodes", json=payload, headers=admin_headers)
    dup = _payload(hostname=payload["hostname"])
    assert client.post("/api/v1/nodes", json=dup, headers=admin_headers).status_code == 409


def test_write_endpoints_require_authentication(viewer_headers):
    # No credentials at all -> 401 from get_current_user.
    assert client.post("/api/v1/nodes", json=_payload()).status_code == 401
    # Logged in but not an admin -> 403 from require_admin.
    assert client.post("/api/v1/nodes", json=_payload(), headers=viewer_headers).status_code == 403


def test_file_sd_reflects_active_nodes(tmp_path, monkeypatch, admin_headers):
    from app.services import prometheus_sd
    monkeypatch.setattr(prometheus_sd, "FILE_SD_PATH", str(tmp_path / "nodes.json"))
    # Unique hostname per run (see _payload's ip_address comment above) --
    # a fixed hostname would 409 against a node left over from a prior run
    # against the same DB, and the file never gets written at all.
    hostname = f"fsd-check-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/nodes", json=_payload(hostname=hostname), headers=admin_headers)
    import json
    data = json.loads((tmp_path / "nodes.json").read_text())
    assert any(t["labels"]["node"] == hostname for t in data)


def test_file_sd_file_is_world_readable(tmp_path, monkeypatch, admin_headers):
    # Prometheus runs as an unprivileged system user; the file the API
    # writes must not be mkstemp's default 0600.
    from app.services import prometheus_sd
    monkeypatch.setattr(prometheus_sd, "FILE_SD_PATH", str(tmp_path / "nodes.json"))
    hostname = f"perm-check-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/nodes", json=_payload(hostname=hostname), headers=admin_headers)
    mode = (tmp_path / "nodes.json").stat().st_mode & 0o777
    assert mode & 0o044 == 0o044  # owner+other read bits set