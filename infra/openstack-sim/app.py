"""Tiny mock of the OpenStack control plane (Keystone + Nova + Neutron)
for sandbox testing. Just enough surface area for openstacksdk's
list/get calls used by topology_sync.py -- not a real OpenStack.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

import os

# Single port for everything -- one uvicorn process serving all three
# "services" behind different path prefixes (/v3, /v2.1, /v2.0), so the
# sandbox container only needs one exposed port instead of three.
SIM_HOST = os.environ.get("OPENSTACK_SIM_HOST", "openstack-sim")
SIM_PORT = os.environ.get("OPENSTACK_SIM_PORT", "5000")
_BASE = f"http://{SIM_HOST}:{SIM_PORT}"
IDENTITY_URL = f"{_BASE}/v3"
COMPUTE_URL = f"{_BASE}/v2.1"
NETWORK_URL = f"{_BASE}/v2.0"

# ---- seed data, mirrors infra/ansible-sandbox's controller-sim/compute*-sim ----
HYPERVISORS = [
    {
        "id": "1",
        "hypervisor_hostname": "compute1-sim",
        "host_ip": "10.0.1.21",
        "state": "up",
        "status": "enabled",
        "vcpus": 8,
        "vcpus_used": 2,
        "memory_mb": 16384,
        "memory_mb_used": 4096,
        "local_gb": 200,
        "local_gb_used": 40,
        "running_vms": 2,
        "hypervisor_type": "QEMU",
        "hypervisor_version": 2011000,
    },
    {
        "id": "2",
        "hypervisor_hostname": "compute2-sim",
        "host_ip": "10.0.1.22",
        "state": "up",
        "status": "enabled",
        "vcpus": 8,
        "vcpus_used": 1,
        "memory_mb": 16384,
        "memory_mb_used": 2048,
        "local_gb": 200,
        "local_gb_used": 20,
        "running_vms": 1,
        "hypervisor_type": "QEMU",
        "hypervisor_version": 2011000,
    },
]

NOVA_SERVICES = [
    {"id": 1, "binary": "nova-compute", "host": "compute1-sim", "zone": "nova", "status": "enabled", "state": "up"},
    {"id": 2, "binary": "nova-compute", "host": "compute2-sim", "zone": "nova", "status": "enabled", "state": "up"},
    {"id": 3, "binary": "nova-scheduler", "host": "controller-sim", "zone": "internal", "status": "enabled", "state": "up"},
    {"id": 4, "binary": "nova-conductor", "host": "controller-sim", "zone": "internal", "status": "enabled", "state": "up"},
]

NETWORKS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000001",
        "name": "sandbox-net",
        "status": "ACTIVE",
        "admin_state_up": True,
        "shared": False,
        "subnets": ["8f3f0f4a-0000-0000-0000-000000000011"],
        "project_id": "sandbox-project",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000002",
        "name": "sandbox-storage-net",
        "status": "ACTIVE",
        "admin_state_up": True,
        "shared": False,
        "subnets": ["8f3f0f4a-0000-0000-0000-000000000012"],
        "project_id": "sandbox-project",
    },
]

SUBNETS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000011",
        "name": "sandbox-subnet",
        "network_id": "8f3f0f4a-0000-0000-0000-000000000001",
        "cidr": "10.0.1.0/24",
        "ip_version": 4,
        "gateway_ip": "10.0.1.1",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000012",
        "name": "sandbox-storage-subnet",
        "network_id": "8f3f0f4a-0000-0000-0000-000000000002",
        "cidr": "10.0.2.0/24",
        "ip_version": 4,
        "gateway_ip": "10.0.2.1",
    },
]

ROUTERS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000021",
        "name": "sandbox-router",
        "status": "ACTIVE",
        "admin_state_up": True,
        "external_gateway_info": None,
        "project_id": "sandbox-project",
    },
]

FLOATING_IPS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000031",
        "floating_ip_address": "203.0.113.10",
        "fixed_ip_address": "10.0.1.21",
        "status": "ACTIVE",
        "floating_network_id": "8f3f0f4a-0000-0000-0000-000000000001",
        "router_id": "8f3f0f4a-0000-0000-0000-000000000021",
    },
]

NEUTRON_AGENTS = [
    {"id": "a1", "binary": "neutron-l3-agent", "host": "controller-sim", "agent_type": "L3 agent", "alive": True, "admin_state_up": True},
    {"id": "a2", "binary": "neutron-dhcp-agent", "host": "controller-sim", "agent_type": "DHCP agent", "alive": True, "admin_state_up": True},
    {"id": "a3", "binary": "neutron-openvswitch-agent", "host": "compute1-sim", "agent_type": "Open vSwitch agent", "alive": True, "admin_state_up": True},
    {"id": "a4", "binary": "neutron-openvswitch-agent", "host": "compute2-sim", "agent_type": "Open vSwitch agent", "alive": True, "admin_state_up": True},
]


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _future_iso(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- identity --
@app.get("/v3")
def identity_version():
    return {
        "version": {
            "id": "v3.14",
            "status": "stable",
            "links": [{"rel": "self", "href": IDENTITY_URL}],
        }
    }


@app.post("/v3/auth/tokens")
async def issue_token(request: Request):
    body = await request.json()
    auth = body.get("auth", {})
    identity = auth.get("identity", {})
    password_auth = identity.get("password", {}).get("user", {})
    username = password_auth.get("name", "unknown")

    token_body = {
        "token": {
            "issued_at": _now_iso(),
            "expires_at": _future_iso(),
            "methods": ["password"],
            "user": {
                "id": "sim-user-id",
                "name": username,
                "domain": {"id": "default", "name": "Default"},
            },
            "project": {
                "id": "sandbox-project",
                "name": "admin",
                "domain": {"id": "default", "name": "Default"},
            },
            "roles": [{"id": "sim-role-id", "name": "reader"}],
            "catalog": [
                {
                    "type": "identity",
                    "name": "keystone",
                    "id": "identity-sim",
                    "endpoints": [
                        {"id": "identity-pub", "interface": "public", "region": "RegionOne", "url": IDENTITY_URL},
                    ],
                },
                {
                    "type": "compute",
                    "name": "nova",
                    "id": "compute-sim",
                    "endpoints": [
                        {"id": "compute-pub", "interface": "public", "region": "RegionOne", "url": COMPUTE_URL},
                    ],
                },
                {
                    "type": "network",
                    "name": "neutron",
                    "id": "network-sim",
                    "endpoints": [
                        {"id": "network-pub", "interface": "public", "region": "RegionOne", "url": NETWORK_URL},
                    ],
                },
            ],
        }
    }
    resp = JSONResponse(content=token_body)
    resp.headers["X-Subject-Token"] = f"sim-token-{uuid.uuid4().hex}"
    return resp


# ------------------------------------------------------------------- nova --
@app.get("/v2.1")
def compute_version():
    return {
        "version": {
            "id": "v2.1",
            "status": "CURRENT",
            "version": "2.90",
            "min_version": "2.1",
            "links": [{"rel": "self", "href": COMPUTE_URL}],
        }
    }


@app.get("/v2.1/os-hypervisors/detail")
def list_hypervisors_detail():
    return {"hypervisors": HYPERVISORS}


@app.get("/v2.1/os-hypervisors")
def list_hypervisors():
    return {"hypervisors": HYPERVISORS}


@app.get("/v2.1/os-hypervisors/{hv_id}")
def get_hypervisor(hv_id: str):
    for hv in HYPERVISORS:
        if hv["id"] == hv_id:
            return {"hypervisor": hv}
    return JSONResponse(status_code=404, content={"error": "not found"})


@app.get("/v2.1/os-services")
def list_services():
    return {"services": NOVA_SERVICES}


# ---------------------------------------------------------------- neutron --
@app.get("/v2.0")
@app.get("/")
def network_version():
    return {
        "version": {
            "id": "v2.0",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": NETWORK_URL}],
        }
    }


@app.get("/v2.0/networks")
def list_networks():
    return {"networks": NETWORKS}


@app.get("/v2.0/subnets")
def list_subnets():
    return {"subnets": SUBNETS}


@app.get("/v2.0/routers")
def list_routers():
    return {"routers": ROUTERS}


@app.get("/v2.0/floatingips")
def list_floating_ips():
    return {"floatingips": FLOATING_IPS}


@app.get("/v2.0/agents")
def list_agents():
    return {"agents": NEUTRON_AGENTS}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
