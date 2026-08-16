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
# Own path prefix (not "/v3", which is already Keystone's) -- mirrors how
# COMPUTE_URL/NETWORK_URL each get their own prefix so version-discovery
# GETs against this endpoint don't collide with the identity ones.
BLOCK_STORAGE_URL = f"{_BASE}/volume/v3"

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

CINDER_SERVICES = [
    # controller-sim runs the control-plane pieces, same as it does for Nova.
    {"id": 1, "binary": "cinder-scheduler", "host": "controller-sim", "zone": "internal", "status": "enabled", "state": "up"},
    {"id": 2, "binary": "cinder-backup", "host": "storage-sim", "zone": "nova", "status": "enabled", "state": "up"},
    # `host@backend` -- exercises topology_sync._parse_cinder_host's split,
    # same as a real multi-backend Cinder deployment would report.
    {"id": 3, "binary": "cinder-volume", "host": "storage-sim@lvmdriver-1", "zone": "nova", "status": "enabled", "state": "up"},
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
        # Gatewayed onto sandbox-net -- exercises topology_sync's
        # Router-[:CONNECTS]->Network edge (via _gateway_network_id).
        "external_gateway_info": {
            "network_id": "8f3f0f4a-0000-0000-0000-000000000001",
            "external_fixed_ips": [
                {"subnet_id": "8f3f0f4a-0000-0000-0000-000000000011", "ip_address": "10.0.1.254"}
            ],
            "enable_snat": True,
        },
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

# DHCP/L3 hosting-endpoint seed data -- what
# GET /v2.0/agents/{agent_id}/dhcp-networks and .../l3-routers return,
# keyed by agent id. Mirrors what conn.network.dhcp_agent_hosting_networks()
# / conn.network.agent_hosted_routers() actually call in a real deployment.
DHCP_AGENT_NETWORKS = {
    # a2 (neutron-dhcp-agent@controller-sim) hosts DHCP for both sandbox networks.
    "a2": [NETWORKS[0], NETWORKS[1]],
}

L3_AGENT_ROUTERS = {
    # a1 (neutron-l3-agent@controller-sim) hosts the one sandbox router.
    "a1": [ROUTERS[0]],
}

# ---- quota/budget monitor seed data (services/quota_budget_monitor.py) ----
# One project (matches the token's scoped project above: id
# "sandbox-project", name "admin"), with quotas from infra.md's "Quotas
# par projet" table (100 VMs / 20 vCPUs / 50 GB RAM / 50 floating IPs,
# unlimited volumes/storage). `totalCoresUsed` is deliberately set to 90%
# of `maxTotalCores` so a fresh sandbox already has one real capacity_cap
# warning to look at (GET /api/v1/quotas/alerts) without needing to hand-
# edit this file first -- everything else is comfortably under its cap.
PROJECTS = [
    {"id": "sandbox-project", "name": "admin", "domain_id": "default", "enabled": True},
]

NOVA_ABSOLUTE_LIMITS = {
    "maxTotalInstances": 100,
    "totalInstancesUsed": 5,
    "maxTotalCores": 20,
    "totalCoresUsed": 18,
    "maxTotalRAMSize": 51200,  # 50 GB, in MB
    "totalRAMUsed": 24576,  # 24 GB
    "maxTotalFloatingIps": 50,
    "totalFloatingIpsUsed": 1,
}

CINDER_ABSOLUTE_LIMITS = {
    "maxTotalVolumes": -1,  # "illimité" per infra.md -- unlimited quota
    "totalVolumesUsed": 3,
    "maxTotalVolumeGigabytes": -1,
    "totalGigabytesUsed": 160,
}


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


@app.get("/v3/projects")
def list_projects():
    # openstacksdk's identity.projects() -- backs
    # quota_budget_monitor._list_projects(). Real Keystone also accepts
    # filters (?domain_id=, ?name=, ...) as query params; the sim ignores
    # them and always returns the one seed project, same simplification
    # every other list endpoint here makes.
    return {"projects": PROJECTS}


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
                {
                    # openstacksdk's block_storage proxy looks this up by
                    # service_type == "block-storage" specifically (see
                    # openstack/_services_mixin.py) -- that's the string
                    # that must match here, "cinder"/"volume" are just
                    # display name / aliases elsewhere, not this.
                    "type": "block-storage",
                    "name": "cinder",
                    "id": "block-storage-sim",
                    "endpoints": [
                        {"id": "block-storage-pub", "interface": "public", "region": "RegionOne", "url": BLOCK_STORAGE_URL},
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


@app.get("/v2.1/limits")
def get_compute_limits(tenant_id: str | None = None):
    # `tenant_id` is what openstacksdk's compute.get_limits(project_id=...)
    # actually sends (Limits._query_mapping maps project_id -> tenant_id,
    # see openstack.compute.v2.limits.Limits) -- accepted and ignored here,
    # same one-project simplification as GET /v3/projects above.
    return {"limits": {"rate": [], "absolute": NOVA_ABSOLUTE_LIMITS}}


# -------------------------------------------------------------- cinder --
@app.get("/volume/v3")
def block_storage_version():
    # keystoneauth1's get_api_major_version() GETs this before trusting the
    # catalog entry (same as it does for Nova's /v2.1) -- without it,
    # conn.block_storage.* calls fail version discovery even once the
    # catalog entry above exists.
    return {
        "version": {
            "id": "v3.0",
            "status": "CURRENT",
            "links": [{"rel": "self", "href": BLOCK_STORAGE_URL}],
        }
    }


@app.get("/volume/v3/os-services")
def list_cinder_services():
    return {"services": CINDER_SERVICES}


@app.get("/volume/v3/limits")
def get_volume_limits(project_id: str | None = None):
    return {"limits": {"rate": [], "absolute": CINDER_ABSOLUTE_LIMITS}}


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


@app.get("/v2.0/agents/{agent_id}/dhcp-networks")
def list_dhcp_agent_networks(agent_id: str):
    return {"networks": DHCP_AGENT_NETWORKS.get(agent_id, [])}


@app.get("/v2.0/agents/{agent_id}/l3-routers")
def list_l3_agent_routers(agent_id: str):
    return {"routers": L3_AGENT_ROUTERS.get(agent_id, [])}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
