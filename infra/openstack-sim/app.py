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
        "vcpus_used": 3,
        "memory_mb": 16384,
        "memory_mb_used": 6144,
        "local_gb": 200,
        "local_gb_used": 60,
        "running_vms": 3,
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
        "vcpus_used": 2,
        "memory_mb": 16384,
        "memory_mb_used": 4096,
        "local_gb": 200,
        "local_gb_used": 40,
        "running_vms": 2,
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
        # Self-service (tenant-owned) network -- not externally reachable
        # on its own, only via sandbox-router's gateway onto
        # sandbox-external-net below. Mirrors real Neutron's
        # `router:external` field (see topology_sync.py's
        # `is_router_external` read); openstacksdk maps this exact key
        # to that attribute.
        "router:external": False,
        "subnets": ["8f3f0f4a-0000-0000-0000-000000000011"],
        "project_id": "sandbox-project",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000002",
        "name": "sandbox-storage-net",
        "status": "ACTIVE",
        "admin_state_up": True,
        "shared": False,
        # Also self-service, but deliberately left un-routed (no router
        # interface onto it below) -- an isolated storage/back-end network
        # is a common real shape, and one with no gateway at all is a
        # useful edge case for the network-topology view to render sanely.
        "router:external": False,
        "subnets": ["8f3f0f4a-0000-0000-0000-000000000012"],
        "project_id": "sandbox-project",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000003",
        "name": "sandbox-external-net",
        "status": "ACTIVE",
        "admin_state_up": True,
        # Shared provider network every project's routers gateway onto --
        # the "public"/"provider" trunk in Horizon's Network Topology view
        # and in NetworkTopologyCanvas.tsx.
        "shared": True,
        "router:external": True,
        "subnets": ["8f3f0f4a-0000-0000-0000-000000000013"],
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
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000013",
        "name": "sandbox-external-subnet",
        "network_id": "8f3f0f4a-0000-0000-0000-000000000003",
        "cidr": "203.0.113.0/24",
        "ip_version": 4,
        "gateway_ip": "203.0.113.1",
    },
]

ROUTERS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000021",
        "name": "sandbox-router",
        "status": "ACTIVE",
        "admin_state_up": True,
        # Gatewayed onto sandbox-external-net (the provider side) --
        # exercises topology_sync's Router-[:CONNECTS]->Network edge (via
        # _gateway_network_id). Its other side, the router-interface port
        # onto sandbox-net's subnet below (PORTS), is the self-service
        # side -- together the two are what let
        # graph_db.fetch_topology_map's interface_router_ids/
        # gateway_router_ids place this one router between both networks
        # on the topology canvas, same as Horizon draws it.
        "external_gateway_info": {
            "network_id": "8f3f0f4a-0000-0000-0000-000000000003",
            "external_fixed_ips": [
                {"subnet_id": "8f3f0f4a-0000-0000-0000-000000000013", "ip_address": "203.0.113.254"}
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
        # Carved from the provider network, same as real Neutron always
        # requires (a floating IP always comes from a router:external=True
        # network) -- not from sandbox-net, which isn't externally routable
        # on its own.
        "floating_network_id": "8f3f0f4a-0000-0000-0000-000000000003",
        "router_id": "8f3f0f4a-0000-0000-0000-000000000021",
    },
]

NEUTRON_AGENTS = [
    # Phase 0 (security scope-clarification roadmap, §1.1): this placement
    # -- l3-agent/dhcp-agent on the controller, OVS on the computes -- is a
    # sandbox-topology choice, not an architectural requirement. This
    # project's Node schema has no dedicated "network" role (only
    # controller/compute/storage/monitoring), so these control-plane
    # agents have to live on one of those four; every downstream reader
    # (network_health.py, topology_sync.py's SERVES-edge sync) resolves an
    # agent by matching its `host` label against a real Node hostname, not
    # by assuming which role hosts which agent -- so moving these two
    # entries to compute{1,2}-sim, if a future deployment wants that
    # instead, is a one-line change here with no code change required.
    {"id": "a1", "binary": "neutron-l3-agent", "host": "controller-sim", "agent_type": "L3 agent", "alive": True, "admin_state_up": True},
    {"id": "a2", "binary": "neutron-dhcp-agent", "host": "controller-sim", "agent_type": "DHCP agent", "alive": True, "admin_state_up": True},
    {"id": "a3", "binary": "neutron-openvswitch-agent", "host": "compute1-sim", "agent_type": "Open vSwitch agent", "alive": True, "admin_state_up": True},
    {"id": "a4", "binary": "neutron-openvswitch-agent", "host": "compute2-sim", "agent_type": "Open vSwitch agent", "alive": True, "admin_state_up": True},
]

# Phase 6 seed data (topology_sync.py's instance/port sync) -- three VMs on
# sandbox-net, two healthy (one per compute-sim node, exercising the
# Instance-[:RUNS_ON]->Node edge across both hypervisors) and one
# deliberately broken (ERROR status + its port DOWN/admin_state_up False),
# so a sync against this sim always has a real problem to show on the
# planned network-topology visualization, the same way ROUTERS/
# NEUTRON_AGENTS above always have one real, working structural edge to
# test against.
SERVERS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000041",
        "name": "sandbox-vm-1",
        "status": "ACTIVE",
        "tenant_id": "sandbox-project",
        # Extended attribute -- see topology_sync.py's Phase 6 docstring on
        # why real Nova sometimes gates this field behind an admin-only
        # policy even when the server listing itself is visible. The sim
        # has no policy engine, so it's always present here.
        "OS-EXT-SRV-ATTR:hypervisor_hostname": "compute1-sim",
        "flavor": {"id": "m1.small", "original_name": "m1.small", "vcpus": 1, "ram": 2048, "disk": 20},
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000042",
        "name": "sandbox-vm-2",
        "status": "ACTIVE",
        "tenant_id": "sandbox-project",
        "OS-EXT-SRV-ATTR:hypervisor_hostname": "compute2-sim",
        "flavor": {"id": "m1.small", "original_name": "m1.small", "vcpus": 1, "ram": 2048, "disk": 20},
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000043",
        "name": "sandbox-vm-3-broken",
        "status": "ERROR",
        "tenant_id": "sandbox-project",
        "OS-EXT-SRV-ATTR:hypervisor_hostname": "compute1-sim",
        "flavor": {"id": "m1.small", "original_name": "m1.small", "vcpus": 1, "ram": 2048, "disk": 20},
    },
    {
        # Phase Sec-5 seed: a second instance on compute1-sim that's on a
        # *different* security group than sandbox-vm-1/-3-broken (below),
        # so this node has more than one group to show, and each group's
        # "which instance(s) actually carry this" list isn't just "all of
        # them" every time.
        "id": "8f3f0f4a-0000-0000-0000-000000000044",
        "name": "sandbox-vm-4-web",
        "status": "ACTIVE",
        "tenant_id": "sandbox-project",
        "OS-EXT-SRV-ATTR:hypervisor_hostname": "compute1-sim",
        "flavor": {"id": "m1.small", "original_name": "m1.small", "vcpus": 1, "ram": 2048, "disk": 20},
    },
    {
        # And one on compute2-sim with its own group, carrying a *different*
        # sensitive-port hit (MySQL, not SSH) -- so the two compute nodes
        # don't just show the same finding twice, and the demo proves the
        # baseline catches more than one specific port.
        "id": "8f3f0f4a-0000-0000-0000-000000000045",
        "name": "sandbox-vm-5-db",
        "status": "ACTIVE",
        "tenant_id": "sandbox-project",
        "OS-EXT-SRV-ATTR:hypervisor_hostname": "compute2-sim",
        "flavor": {"id": "m1.small", "original_name": "m1.small", "vcpus": 1, "ram": 2048, "disk": 20},
    },
]

PORTS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000051",
        "name": "sandbox-vm-1-port",
        "status": "ACTIVE",
        "admin_state_up": True,
        "mac_address": "fa:16:3e:00:00:51",
        "device_id": SERVERS[0]["id"],
        "device_owner": "compute:nova",
        "network_id": NETWORKS[0]["id"],
        "fixed_ips": [{"subnet_id": SUBNETS[0]["id"], "ip_address": "10.0.1.101"}],
        "tenant_id": "sandbox-project",
        # Phase Sec-1/Sec-2: this port carries the "default" security group
        # (SECURITY_GROUPS below) -- what security_audit.get_node_security_groups
        # and security_snapshot_builder.py's periodic pass both key off of
        # (openstacksdk's port.security_group_ids).
        "security_group_ids": ["8f3f0f4a-0000-0000-0000-0000000000e1"],
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-000000000052",
        "name": "sandbox-vm-2-port",
        "status": "ACTIVE",
        "admin_state_up": True,
        "mac_address": "fa:16:3e:00:00:52",
        "device_id": SERVERS[1]["id"],
        "device_owner": "compute:nova",
        "network_id": NETWORKS[0]["id"],
        "fixed_ips": [{"subnet_id": SUBNETS[0]["id"], "ip_address": "10.0.1.102"}],
        "tenant_id": "sandbox-project",
        "security_group_ids": ["8f3f0f4a-0000-0000-0000-0000000000e1"],
    },
    {
        # The broken pairing: sandbox-vm-3-broken's port is DOWN and
        # admin-disabled -- a realistic combination (a failed port bind
        # commonly leaves the instance stuck in ERROR too).
        "id": "8f3f0f4a-0000-0000-0000-000000000053",
        "name": "sandbox-vm-3-port",
        "status": "DOWN",
        "admin_state_up": False,
        "mac_address": "fa:16:3e:00:00:53",
        "device_id": SERVERS[2]["id"],
        "device_owner": "compute:nova",
        "network_id": NETWORKS[0]["id"],
        "fixed_ips": [{"subnet_id": SUBNETS[0]["id"], "ip_address": "10.0.1.103"}],
        "tenant_id": "sandbox-project",
        "security_group_ids": ["8f3f0f4a-0000-0000-0000-0000000000e1"],
    },
    {
        # sandbox-vm-4-web: on *both* "default" and the new "web-frontend"
        # group, so "default" reads as compute1-sim's shared baseline group
        # (all three of its VMs carry it) while "web-frontend" is scoped to
        # just this one instance.
        "id": "8f3f0f4a-0000-0000-0000-000000000055",
        "name": "sandbox-vm-4-web-port",
        "status": "ACTIVE",
        "admin_state_up": True,
        "mac_address": "fa:16:3e:00:00:55",
        "device_id": SERVERS[3]["id"],
        "device_owner": "compute:nova",
        "network_id": NETWORKS[0]["id"],
        "fixed_ips": [{"subnet_id": SUBNETS[0]["id"], "ip_address": "10.0.1.104"}],
        "tenant_id": "sandbox-project",
        "security_group_ids": [
            "8f3f0f4a-0000-0000-0000-0000000000e1",
            "8f3f0f4a-0000-0000-0000-0000000000e2",
        ],
    },
    {
        # sandbox-vm-5-db: "database" only -- deliberately *not* on
        # "default", so compute2-sim shows a group set with no overlap
        # against compute1-sim's, instead of every node just being a
        # variation on the same one group.
        "id": "8f3f0f4a-0000-0000-0000-000000000056",
        "name": "sandbox-vm-5-db-port",
        "status": "ACTIVE",
        "admin_state_up": True,
        "mac_address": "fa:16:3e:00:00:56",
        "device_id": SERVERS[4]["id"],
        "device_owner": "compute:nova",
        "network_id": NETWORKS[0]["id"],
        "fixed_ips": [{"subnet_id": SUBNETS[0]["id"], "ip_address": "10.0.1.105"}],
        "tenant_id": "sandbox-project",
        "security_group_ids": ["8f3f0f4a-0000-0000-0000-0000000000e3"],
    },
    {
        # sandbox-router's internal interface onto sandbox-net's subnet --
        # device_owner starts with "network:router_interface" and
        # device_id is the router's own id, exactly the shape
        # graph_db.fetch_topology_map's interface_router_ids traversal
        # looks for to find which router(s) sit "below" a self-service
        # network. Sits at the subnet's own gateway_ip, same as real
        # Neutron always places a router interface port.
        "id": "8f3f0f4a-0000-0000-0000-000000000054",
        "name": "sandbox-router-interface",
        "status": "ACTIVE",
        "admin_state_up": True,
        "mac_address": "fa:16:3e:00:00:54",
        "device_id": ROUTERS[0]["id"],
        "device_owner": "network:router_interface",
        "network_id": NETWORKS[0]["id"],
        "fixed_ips": [{"subnet_id": SUBNETS[0]["id"], "ip_address": "10.0.1.1"}],
        "tenant_id": "sandbox-project",
    },
]

# Phase Sec-1/Sec-2 seed data: one security group ("default", attached to
# both healthy sandbox VMs above) with a real world-open SSH rule --
# security_audit._risk_reason flags this immediately (a genuine "does
# this look risky right now" hit against the sim, not a mocked fixture),
# and security_snapshot_builder.py's periodic pass gives
# `_check_sec_group_diff` a real snapshot to diff future changes against.
# `/_sandbox/security-group-rule/add` and `/remove` below let a test (or
# a person poking at the sandbox) actually mutate this list on demand to
# exercise the Sec-1 drift path end to end -- add a rule, wait for the
# next snapshot pass (or trigger one via run_security_snapshot.py), ask
# the Security Agent again, see the diff.
SECURITY_GROUPS = [
    {
        "id": "8f3f0f4a-0000-0000-0000-0000000000e1",
        "name": "default",
        "description": "Default security group for the sandbox project",
        "project_id": "sandbox-project",
    },
    {
        # Phase Sec-5 seed: world-open HTTPS is normal for a public web
        # tier, so this group is a deliberate *negative* example -- one
        # rule below is world-open and still correctly reads "Clean" in
        # the UI, since 443 isn't in security_audit._SENSITIVE_PORTS.
        "id": "8f3f0f4a-0000-0000-0000-0000000000e2",
        "name": "web-frontend",
        "description": "Public HTTPS ingress for the sandbox web tier",
        "project_id": "sandbox-project",
    },
    {
        # And a second real positive example, on a different sensitive
        # port than "default"'s SSH hit, so the demo doesn't just repeat
        # the same one finding on every group.
        "id": "8f3f0f4a-0000-0000-0000-0000000000e3",
        "name": "database",
        "description": "MySQL ingress for the sandbox database tier",
        "project_id": "sandbox-project",
    },
]

SECURITY_GROUP_RULES = [
    {
        "id": "8f3f0f4a-0000-0000-0000-0000000000f1",
        "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e1",
        "direction": "ingress",
        "ethertype": "IPv4",
        "protocol": "tcp",
        "port_range_min": 22,
        "port_range_max": 22,
        # World-open SSH -- security_audit._risk_reason's textbook case,
        # seeded on purpose so a fresh sandbox always has one real
        # "overly-permissive" finding to look at, the same way ROUTERS/
        # SERVERS above always have one real broken/degraded case.
        "remote_ip_prefix": "0.0.0.0/0",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-0000000000f2",
        "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e1",
        "direction": "egress",
        "ethertype": "IPv4",
        "protocol": None,
        "port_range_min": None,
        "port_range_max": None,
        # Open egress is normal/expected (see security_audit._risk_reason's
        # own "ingress-only" docstring note) -- kept here so the rule-set
        # isn't unrealistically ingress-only.
        "remote_ip_prefix": "0.0.0.0/0",
    },
    {
        # web-frontend: world-open HTTPS -- expected for a public web tier,
        # not in _SENSITIVE_PORTS, so this is the sim's one deliberately
        # *unflagged* world-open rule (see SECURITY_GROUPS' comment above).
        "id": "8f3f0f4a-0000-0000-0000-0000000000f3",
        "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e2",
        "direction": "ingress",
        "ethertype": "IPv4",
        "protocol": "tcp",
        "port_range_min": 443,
        "port_range_max": 443,
        "remote_ip_prefix": "0.0.0.0/0",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-0000000000f4",
        "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e2",
        "direction": "egress",
        "ethertype": "IPv4",
        "protocol": None,
        "port_range_min": None,
        "port_range_max": None,
        "remote_ip_prefix": "0.0.0.0/0",
    },
    {
        # database: world-open MySQL -- the second real "overly-permissive"
        # finding, on a different port than "default"'s SSH one.
        "id": "8f3f0f4a-0000-0000-0000-0000000000f5",
        "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e3",
        "direction": "ingress",
        "ethertype": "IPv4",
        "protocol": "tcp",
        "port_range_min": 3306,
        "port_range_max": 3306,
        "remote_ip_prefix": "0.0.0.0/0",
    },
    {
        "id": "8f3f0f4a-0000-0000-0000-0000000000f6",
        "security_group_id": "8f3f0f4a-0000-0000-0000-0000000000e3",
        "direction": "egress",
        "ethertype": "IPv4",
        "protocol": None,
        "port_range_min": None,
        "port_range_max": None,
        "remote_ip_prefix": "0.0.0.0/0",
    },
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


@app.get("/v2.1/servers/detail")
def list_servers_detail():
    # openstacksdk's compute.servers(details=True) hits this path.
    # Real Nova also accepts filters (?status=, ?host=, ?all_tenants=,
    # ...) as query params; the sim ignores them and always returns the
    # full seed list, same simplification every other list endpoint here
    # makes. See topology_sync.py's Phase 6 docstring for why
    # all_projects/all_tenants isn't something Cortex actually asks for.
    return {"servers": SERVERS}


@app.get("/v2.1/servers")
def list_servers():
    return {"servers": SERVERS}


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
    return {"routers": [{**r, **_ROUTER_FAULTS.get(r["id"], {})} for r in ROUTERS]}


@app.get("/v2.0/floatingips")
def list_floating_ips():
    return {
        "floatingips": [
            {**f, **_FLOATINGIP_FAULTS.get(f["id"], {})} for f in FLOATING_IPS
        ]
    }


@app.get("/v2.0/agents")
def list_agents():
    return {"agents": NEUTRON_AGENTS}


@app.get("/v2.0/agents/{agent_id}/dhcp-networks")
def list_dhcp_agent_networks(agent_id: str):
    return {"networks": DHCP_AGENT_NETWORKS.get(agent_id, [])}


@app.get("/v2.0/agents/{agent_id}/l3-routers")
def list_l3_agent_routers(agent_id: str):
    return {"routers": L3_AGENT_ROUTERS.get(agent_id, [])}


@app.get("/v2.0/ports")
def list_ports():
    return {"ports": [{**p, **_PORT_FAULTS.get(p["id"], {})} for p in PORTS]}


@app.get("/v2.0/security-groups")
def list_security_groups():
    # openstacksdk's network.security_groups() -- security_audit.py's
    # get_node_security_groups()/list_security_groups_by_hostname() both
    # call this to resolve a security group's own name.
    return {"security_groups": SECURITY_GROUPS}


@app.get("/v2.0/security-group-rules")
def list_security_group_rules():
    # openstacksdk's network.security_group_rules() -- includes whatever
    # /_sandbox/security-group-rule/add or /remove below has changed
    # in-memory, so a security_snapshot_builder.py pass (or a fresh
    # security_audit.get_node_security_groups() call) run after a fault
    # injection sees the mutated rule-set, exactly like a real Neutron
    # would after `openstack security group rule create/delete`.
    return {"security_group_rules": list(_SECURITY_GROUP_RULES_LIVE.values())}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---- sandbox-only fault injection ----------------------------------------
# Everything above this line mirrors real Neutron read APIs against static
# seed data. There's no way to exercise story 3.6's actual anomaly paths
# (routers_down / floating_ips_orphaned / ports_down in
# graph_db.fetch_network_anomalies, sourced from topology_sync's periodic
# graph sync of these same lists) without a way to flip a resource's state
# on demand. These endpoints do exactly that -- in-memory overrides merged
# into the GET responses above, never touching real OpenStack semantics.
# Not part of any real OpenStack API; only ever call these against the
# sandbox, and only from test scripts/ansible, never from the Cortex API
# itself.
_ROUTER_FAULTS: dict[str, dict] = {}
_PORT_FAULTS: dict[str, dict] = {}
_FLOATINGIP_FAULTS: dict[str, dict] = {}
# Phase Sec-1/Sec-2 real-life drift scenario: a mutable copy of
# SECURITY_GROUP_RULES that /_sandbox/security-group-rule/add and /remove
# actually mutate, so a security_snapshot_builder.py pass taken before the
# mutation and the Security Agent's live `_check_sec_group_diff` read
# taken after it genuinely disagree -- the same "did this change since we
# last looked" scenario Phase Sec-1 exists to catch, exercised against
# real (if simulated) Neutron reads end to end rather than mocked fixtures.
_SECURITY_GROUP_RULES_LIVE: dict[str, dict] = {r["id"]: dict(r) for r in SECURITY_GROUP_RULES}


@app.post("/_sandbox/security-group-rule/add")
def add_security_group_rule(body: dict):
    """body: a full security-group-rule dict (id, security_group_id,
    direction, ethertype, protocol, port_range_min/max, remote_ip_prefix)
    -- same shape security_audit._rule_to_dict produces reading a real
    one. `id` is required and must be unique; this sandbox has no
    auto-generated-id convenience the way real Neutron's rule-create call
    does, since tests/scripts driving this want a stable id to assert
    against afterward.
    """
    rule_id = body.get("id")
    if not rule_id:
        return JSONResponse(status_code=400, content={"error": "body.id is required"})
    _SECURITY_GROUP_RULES_LIVE[rule_id] = dict(body)
    return {"security_group_rule": _SECURITY_GROUP_RULES_LIVE[rule_id]}


@app.post("/_sandbox/security-group-rule/remove")
def remove_security_group_rule(body: dict):
    """body: {"id": "<rule-id>"} -- removes one rule from the live
    rule-set, the other half of the Sec-1 drift scenario (a rule that
    existed at the last snapshot and is now gone)."""
    rule_id = body.get("id")
    _SECURITY_GROUP_RULES_LIVE.pop(rule_id, None)
    return {"removed": rule_id}


@app.post("/_sandbox/fault/router/{router_id}")
def fault_router(router_id: str, body: dict):
    """body: {\"status\": \"DOWN\"} (or any non-ACTIVE value) to trip
    graph_db.fetch_network_anomalies' routers-not-ACTIVE check on the next
    topology_sync pass."""
    _ROUTER_FAULTS[router_id] = {"status": body.get("status", "DOWN")}
    return {"router_id": router_id, "override": _ROUTER_FAULTS[router_id]}


@app.post("/_sandbox/fault/port/{port_id}")
def fault_port(port_id: str, body: dict):
    """body: {\"status\": \"DOWN\"} to trip the ports-not-ACTIVE check."""
    _PORT_FAULTS[port_id] = {"status": body.get("status", "DOWN")}
    return {"port_id": port_id, "override": _PORT_FAULTS[port_id]}


@app.post("/_sandbox/fault/floatingip/{fip_id}")
def fault_floating_ip(fip_id: str, body: dict | None = None):
    """Detaches the floating IP from its router (router_id: null), which is
    exactly the \"no CONNECTS edge to a router\" condition
    fetch_network_anomalies' floating_ips_orphaned check looks for."""
    _FLOATINGIP_FAULTS[fip_id] = {"router_id": None, "port_id": None}
    return {"floatingip_id": fip_id, "override": _FLOATINGIP_FAULTS[fip_id]}


@app.post("/_sandbox/fault/reset")
def fault_reset():
    """Clears every override above, restoring all seed data to its healthy
    default state -- including the live security-group rule-set back to
    SECURITY_GROUP_RULES's original seed (undoes any
    /_sandbox/security-group-rule/add or /remove call)."""
    _ROUTER_FAULTS.clear()
    _PORT_FAULTS.clear()
    _FLOATINGIP_FAULTS.clear()
    _SECURITY_GROUP_RULES_LIVE.clear()
    _SECURITY_GROUP_RULES_LIVE.update({r["id"]: dict(r) for r in SECURITY_GROUP_RULES})
    return {"status": "reset"}
