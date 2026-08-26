"""Curated symptom -> command -> doc catalog for the OpenStack Expert Agent
(v0.6, see docs/architecture/adr-0008-openstack-expert-agent.md).

This is data, not logic: a fixed list of `SymptomEntry` records, each one
"what's happening / how to confirm it yourself / what's usually done about
it" for one recognizable failure mode on this specific cloud (2024.1
Caracal via Kolla-Ansible, Docker-containerized services, KVM/QEMU +
libvirt, Open vSwitch, RabbitMQ, MariaDB -- see docs/knowledge/README.md).
`openstack_expert.py` is the only thing that reads this list; it never
writes to it, and every command in it is a real, correct command for this
stack -- nothing here is a placeholder or an invented example.

Coverage is deliberately weighted two ways, per the v0.6 brief:

1. **What Sprint 1 anomaly detection actually flags today.**
   `anomaly_detector.py`'s METRICS dict only scores `cpu_usage` and
   `ram_usage` (host-level, via node_exporter) -- entries 1-2 below are
   the ones a live incident in this environment will hit most often, so
   they get the deepest treatment. `disk_usage` isn't scored yet (no
   node_exporter-backed anomaly detection for it), but it's common enough
   operationally that it's included anyway, clearly noted as such.
2. **What `prometheus_health.py` (ADR-0003) and `topology_sync.py`
   already know how to detect independently of AnomalyFlag**: a Node
   going Prometheus-unreachable, or a specific OpenStack service
   (nova-compute, nova-scheduler, cinder-volume, cinder-scheduler,
   neutron-dhcp-agent, neutron-l3-agent, neutron-openvswitch-agent --
   the exact binaries topology_sync.py syncs, see its module docstring)
   reconciling to `unreachable`. These are graph-derived signals, not
   AnomalyFlag rows, but they're just as real an incident trigger.

Everything past that (Nova scheduling failures, stuck instances, Glance
image issues, RabbitMQ/MariaDB/Keystone problems, libvirt/hypervisor
issues) is general OpenStack operational knowledge that doesn't yet have
an automatic Cortex detector behind it, but is exactly the kind of thing
an operator asks Cortex directly ("how do I check X") -- see the
standalone-question half of the agent's job.

`doc_ref` points at the *intended* location in docs/knowledge/ per that
directory's own README table (topology.md, service-detail/nova.md, etc.)
-- those files are the knowledge base's documented target structure but
aren't all authored yet in this checkout, so treat `doc_ref` as "this is
where the deep-dive will live once written", not a guarantee the RAG
agent can retrieve it today. Once a file exists and is ingested, this
same path is exactly what rag_agent's citations will show, so nothing
here needs to change when that happens.
"""
from typing import Literal, TypedDict

Category = Literal[
    "compute", "storage", "network", "identity", "image",
    "message-bus", "database", "hypervisor", "host",
]


class Command(TypedDict):
    command: str
    description: str
    # True: safe to run any time, changes nothing (status/list/show/logs/
    # metrics reads). False: changes running state (restart, disable,
    # reboot, reset, evacuate) -- always run confirm_commands first.
    read_only: bool


class SymptomEntry(TypedDict):
    id: str
    title: str
    category: Category
    # Trigger inputs the symptom-matcher scores against -- see
    # openstack_expert.py's _match_symptoms. Any of these three can fire a
    # match; a real incident's diagnosis usually hits metric_names and/or
    # log_keywords, a standalone question usually hits keywords.
    metric_names: list[str]  # anomaly/monitoring metric_name, e.g. "cpu_usage"
    service_binaries: list[str]  # topology Service.binary, e.g. "nova-compute"
    keywords: list[str]  # free-text phrases: log content or a direct question
    what_it_means: str  # layer 1
    confirm_commands: list[Command]  # layer 2 -- MUST all be read_only=True
    remediation_commands: list[Command]  # layer 3 -- mixed
    doc_ref: str


CATALOG: list[SymptomEntry] = [
    # ------------------------------------------------------------------
    # 1-2: Sprint 1's actual anomaly-detector metrics (cpu_usage, ram_usage)
    # ------------------------------------------------------------------
    {
        "id": "host-cpu-pressure",
        "title": "Host CPU usage flagged high/critical",
        "category": "host",
        "metric_names": ["cpu_usage"],
        "service_binaries": [],
        "keywords": ["high cpu", "cpu usage", "cpu pressure", "cpu spike", "cpu 100%", "cpu maxed"],
        "what_it_means": (
            "Sustained high CPU on a compute node means either genuine workload (VMs actually "
            "doing CPU-heavy work), CPU overcommit (more vCPUs scheduled across guests than the "
            "host has physical cores/threads for -- normal in a private cloud sized for burst, "
            "not sustained 100%), or a single runaway process (host-side or inside a guest) "
            "pinning a core. On a controller/storage node, sustained high CPU is more often a "
            "stuck or looping service process than overcommit, since those nodes don't run guest "
            "VMs."
        ),
        "confirm_commands": [
            {
                "command": "top -o %CPU  (or: ps aux --sort=-%cpu | head -15)",
                "description": "Identify which process is actually driving the load -- a specific qemu-kvm PID (one guest), or something host-side.",
                "read_only": True,
            },
            {
                "command": "virsh list --all   # run inside the nova_libvirt container: docker exec nova_libvirt virsh list --all",
                "description": "On a compute node, cross-check top's qemu-kvm PID against the running domains to see which guest instance it belongs to.",
                "read_only": True,
            },
            {
                "command": "openstack hypervisor show <hypervisor_hostname>",
                "description": "Compare vcpus_used against vcpus (allocation ratio) to tell overcommit-by-design apart from an unexpected spike.",
                "read_only": True,
            },
            {
                "command": "mpstat -P ALL 1 5",
                "description": "Per-core breakdown -- one core pegged at 100% while others idle points to a single-threaded runaway process, not general overcommit.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "openstack server show <instance_id>  (then, if the guest itself is the runaway process) openstack server reboot <instance_id>",
                "description": "If a specific guest is identified as the runaway process, a soft reboot from inside the guest (or, failing that, `openstack server reboot`) usually clears a stuck process.",
                "read_only": False,
            },
            {
                "command": "openstack compute service set --disable --disable-reason \"investigating CPU pressure\" <host> nova-compute",
                "description": "If the host itself is at risk of becoming unresponsive, disable new scheduling onto it while you investigate, without evacuating existing guests.",
                "read_only": False,
            },
            {
                "command": "kill -TERM <pid>  (host-side runaway process only -- never a qemu-kvm PID, that's a live guest)",
                "description": "Only for a confirmed host-side stuck process (not a guest) that top identified -- prefer restarting the owning service over a bare kill where one exists.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/admin-runbook.md",
    },
    {
        "id": "host-ram-pressure",
        "title": "Host RAM usage flagged high/critical",
        "category": "host",
        "metric_names": ["ram_usage"],
        "service_binaries": [],
        "keywords": ["high ram", "high memory", "memory usage", "out of memory", "oom", "memory pressure", "ram full"],
        "what_it_means": (
            "High memory usage on a compute node is either legitimate guest memory allocation "
            "(RAM, unlike CPU, is normally NOT overcommitted in a KVM deployment sized for "
            "stability -- see resource-mgmt.md's flavor definitions), a memory leak in a host-side "
            "service or a long-running guest, or the kernel's OOM killer having already started "
            "reaping processes to cope. On a controller node, this is more often RabbitMQ/MariaDB "
            "connection buildup or an API service leaking memory under load than guest allocation."
        ),
        "confirm_commands": [
            {
                "command": "free -h",
                "description": "Confirm actual used vs. available vs. buff/cache -- a lot of memory reported as \"used\" by page cache is normal and reclaimable, not pressure.",
                "read_only": True,
            },
            {
                "command": "dmesg -T | grep -i 'killed process\\|out of memory'",
                "description": "Check whether the kernel OOM killer has already fired -- if so, this is confirmed OOM, not just a high reading.",
                "read_only": True,
            },
            {
                "command": "ps aux --sort=-%mem | head -15",
                "description": "Identify the single largest consumer -- a specific qemu-kvm process (one guest's allocation) vs. a host-side service.",
                "read_only": True,
            },
            {
                "command": "openstack hypervisor show <hypervisor_hostname>",
                "description": "Compare memory_used against memory_size to see whether guest allocation alone explains the pressure.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart <service_container>  (e.g. nova_api, neutron_server -- whichever host-side service dmesg/ps identified)",
                "description": "A leaking host-side service container is usually fixed by a restart while the underlying leak is investigated/patched.",
                "read_only": False,
            },
            {
                "command": "openstack server migrate --live <target_host> <instance_id>",
                "description": "If a specific guest's legitimate allocation is what's driving pressure and the host needs headroom, live-migrate it to a less-loaded compute node instead of stopping it.",
                "read_only": False,
            },
            {
                "command": "openstack compute service set --disable <host> nova-compute",
                "description": "Stop new instances scheduling onto an already memory-pressured host while you resolve it.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/admin-runbook.md",
    },
    {
        "id": "host-disk-pressure",
        "title": "Host disk usage high or filling up",
        "category": "host",
        "metric_names": ["disk_usage"],  # not yet scored by anomaly_detector.py -- see module docstring
        "service_binaries": [],
        "keywords": ["disk full", "disk usage", "no space left", "enospc", "out of disk", "low disk space"],
        "what_it_means": (
            "Cortex's anomaly detector doesn't score disk_usage yet (only cpu_usage/ram_usage are "
            "in anomaly_detector.py's METRICS today), so this is never an AnomalyFlag-triggered "
            "finding -- but it's one of the most common real OpenStack incidents, so it's worth "
            "checking directly. On a compute node the usual causes are guest disk images/snapshots "
            "(local storage backing, if not fully offloaded to Cinder), Docker's own image/log "
            "layers, or log files that were never rotated. On a storage node, it's Cinder volume "
            "backing files or LVM thin-pool exhaustion."
        ),
        "confirm_commands": [
            {
                "command": "df -h",
                "description": "Which filesystem is actually full -- root, /var/lib/docker, or the Cinder/instance storage mount, since the fix differs for each.",
                "read_only": True,
            },
            {
                "command": "du -sh /var/lib/docker/* 2>/dev/null | sort -rh | head -10",
                "description": "If /var/lib/docker is the culprit: Docker image layers, container logs, or volumes are the usual suspects.",
                "read_only": True,
            },
            {
                "command": "docker system df",
                "description": "Docker's own breakdown of images/containers/volumes/build-cache disk usage -- faster than walking the filesystem by hand.",
                "read_only": True,
            },
            {
                "command": "journalctl --disk-usage",
                "description": "systemd journal logs are a common, easily-missed disk hog on a long-uptime host.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker system prune -a --volumes  (CAUTION: removes ALL unused images/containers/volumes, not just old ones -- review `docker system df` first)",
                "description": "Reclaims space from stopped containers, dangling images, and unused volumes once you've confirmed Docker is the actual cause.",
                "read_only": False,
            },
            {
                "command": "journalctl --vacuum-size=500M",
                "description": "Trims the systemd journal to a fixed size -- safe, standard first step for journal-driven disk pressure.",
                "read_only": False,
            },
            {
                "command": "openstack server migrate --live <target_host> <instance_id>  (only if disk pressure is instance-image-backed and the host needs relief now)",
                "description": "Buys time by moving a guest's footprint off a nearly-full host while the underlying disk is cleaned up or expanded.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/admin-runbook.md",
    },
    # ------------------------------------------------------------------
    # 4-10: OpenStack services topology_sync.py/prometheus_health.py
    # already track (Service.binary / Service.state == "unreachable")
    # ------------------------------------------------------------------
    {
        "id": "nova-compute-down",
        "title": "nova-compute service down or unreachable on a hypervisor",
        "category": "compute",
        "metric_names": [],
        "service_binaries": ["nova-compute"],
        "keywords": ["nova-compute down", "compute service down", "hypervisor unreachable", "nova compute not running"],
        "what_it_means": (
            "nova-compute is the agent on each hypervisor that actually creates/manages guest "
            "domains via libvirt and reports capacity/state back to the scheduler. If it's down, "
            "Nova stops scheduling new instances onto that host (existing guests keep running -- "
            "libvirt/qemu don't depend on nova-compute staying up), and the host's reported state "
            "goes stale. Cortex's own Prometheus cross-check (ADR-0003) marks the Service "
            "`unreachable` specifically when OpenStack's own service table still says `up` but the "
            "host itself has stopped answering node_exporter scrapes -- that's a stronger signal "
            "than nova-compute's own self-reported state, which can lag by its report interval."
        ),
        "confirm_commands": [
            {
                "command": "openstack compute service list --service nova-compute",
                "description": "OpenStack's own view -- State (up/down) and Status (enabled/disabled) for every hypervisor.",
                "read_only": True,
            },
            {
                "command": "docker ps -a --filter name=nova_compute",
                "description": "Is the container itself running, restarting, or exited? A crash-looping container shows as repeated recent restarts.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 nova_compute",
                "description": "The actual error the container is hitting -- common ones: can't reach RabbitMQ, can't reach libvirt, or a config error since the last deploy.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart nova_compute",
                "description": "First thing to try for a hung or crash-looping container, once the logs above rule out a config problem that a restart won't fix.",
                "read_only": False,
            },
            {
                "command": "openstack compute service set --disable --disable-reason \"host unreachable, investigating\" <host> nova-compute",
                "description": "Prevent the scheduler from placing new instances on a host that's confirmed down while you work the issue, without affecting already-running guests.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/nova.md",
    },
    {
        "id": "nova-scheduler-conductor-issue",
        "title": "Instances stuck in BUILD or failing to schedule (\"No valid host was found\")",
        "category": "compute",
        "metric_names": [],
        "service_binaries": ["nova-scheduler"],
        "keywords": ["no valid host was found", "stuck in build", "scheduling failed", "nova-scheduler", "instance won't build"],
        "what_it_means": (
            "\"No valid host was found\" means nova-scheduler evaluated every enabled, up "
            "hypervisor against the requested flavor/AZ/image and none passed its filters -- "
            "usually genuine resource exhaustion (no host with enough free vCPU/RAM/disk for the "
            "requested flavor), an availability-zone mismatch, or every hypervisor that could fit "
            "it being administratively disabled. An instance stuck in BUILD past a few minutes "
            "(rather than immediately erroring) more often points to nova-conductor or the image "
            "download/prep step (Glance) being slow or stuck than scheduling itself."
        ),
        "confirm_commands": [
            {
                "command": "openstack compute service list",
                "description": "Confirm which hosts are actually State=up and Status=enabled -- a disabled or down host is silently excluded from scheduling.",
                "read_only": True,
            },
            {
                "command": "openstack hypervisor stats show",
                "description": "Cluster-wide free vCPU/RAM/disk -- tells you immediately if this is genuine capacity exhaustion.",
                "read_only": True,
            },
            {
                "command": "openstack server show <instance_id>",
                "description": "The `fault` field (if present) usually contains the scheduler's own rejection reason verbatim.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 nova_scheduler",
                "description": "Per-filter rejection detail (RetryFilter, AggregateInstanceExtraSpecsFilter, etc.) that `openstack server show` alone won't have.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "openstack compute service set --enable <host> nova-compute",
                "description": "If a host that should be schedulable is unexpectedly disabled, re-enable it -- the most common one-line fix for this symptom.",
                "read_only": False,
            },
            {
                "command": "openstack server delete <instance_id>  (for an instance permanently stuck in BUILD with no path forward)",
                "description": "Once the underlying capacity/config issue is understood and fixed, a genuinely stuck BUILD instance usually needs to be deleted and re-created rather than recovered in place.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/nova.md",
    },
    {
        "id": "instance-error-state",
        "title": "An instance is in ERROR state",
        "category": "compute",
        "metric_names": [],
        "service_binaries": [],
        "keywords": ["instance error", "server error state", "vm in error", "instance failed"],
        "what_it_means": (
            "ERROR is Nova's terminal state for an instance whose last lifecycle operation "
            "(build, resize, migrate, reboot) failed outright, as opposed to BUILD (still in "
            "progress) or ACTIVE-but-unhealthy (the guest OS itself has a problem Nova doesn't "
            "know about). The specific cause is almost always in the instance's own fault message "
            "or the compute node's nova-compute log from around the time it failed."
        ),
        "confirm_commands": [
            {
                "command": "openstack server show <instance_id>",
                "description": "The `fault` field gives Nova's own recorded reason (a traceback message, a libvirt error, or a scheduling failure) for the ERROR transition.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 300 nova_compute  # on the hypervisor the instance was on/scheduled to",
                "description": "The compute-side detail behind the fault -- libvirt/qemu errors, image download failures, or a network setup failure during spawn.",
                "read_only": True,
            },
            {
                "command": "docker exec nova_libvirt virsh list --all",
                "description": "Check whether libvirt actually has a domain for this instance at all -- its absence confirms the failure happened before or during domain creation.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "openstack server reset-state --active <instance_id>  (only once the underlying cause is understood/fixed)",
                "description": "Forces Nova's state back to ACTIVE without touching the guest -- use only when you've confirmed the guest is actually fine and this is just a stuck state record, never as a first response.",
                "read_only": False,
            },
            {
                "command": "openstack server rebuild <instance_id> <image>",
                "description": "For an instance that failed during initial build with no usable guest to recover, rebuild from the same (or a fixed) image rather than leaving it in ERROR.",
                "read_only": False,
            },
            {
                "command": "openstack server delete <instance_id>",
                "description": "If the fault points to a permanent problem (deleted image, invalid config) that rebuilding won't fix, delete and recreate with corrected parameters.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/nova.md",
    },
    {
        "id": "cinder-volume-down",
        "title": "cinder-volume service down, or volume stuck creating/attaching",
        "category": "storage",
        "metric_names": [],
        "service_binaries": ["cinder-volume", "cinder-scheduler"],
        "keywords": ["cinder-volume down", "volume stuck", "volume creating forever", "attach failed", "block storage down"],
        "what_it_means": (
            "cinder-volume manages the actual backend (LVM in this deployment, per "
            "service-detail/cinder.md) that block volumes are carved from; cinder-scheduler picks "
            "which backend/host handles a new volume request. If cinder-volume is down, existing "
            "attached volumes keep working (they're already connected via the transport layer, "
            "iSCSI in this setup), but new create/attach/detach/snapshot operations will hang or "
            "fail. A volume stuck in `creating` past a minute or two almost always means the "
            "backend (LVM thin pool) is out of space or cinder-volume itself isn't running."
        ),
        "confirm_commands": [
            {
                "command": "openstack volume service list",
                "description": "State/Status for cinder-scheduler and cinder-volume on every backend host -- the same shape as `compute service list` for Nova.",
                "read_only": True,
            },
            {
                "command": "openstack volume show <volume_id>",
                "description": "Current status (creating/available/error/attaching) and, on failure, Cinder's own recorded reason.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 cinder_volume",
                "description": "The actual backend error -- most commonly an LVM thin-pool-full condition or a lost connection to the storage node.",
                "read_only": True,
            },
            {
                "command": "vgs && lvs   # on the storage node, LVM's own view of pool capacity",
                "description": "Directly confirms whether the thin pool backing Cinder volumes has run out of space, independent of what Cinder itself reports.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart cinder_volume",
                "description": "First step for a hung or crash-looping cinder-volume container, once logs rule out a full backend (a restart won't fix that).",
                "read_only": False,
            },
            {
                "command": "openstack volume delete <volume_id>  (for a volume stuck in `error`/`creating` with no valid backing data)",
                "description": "A volume that failed during creation with no usable data typically needs to be deleted and recreated rather than repaired in place.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/cinder.md",
    },
    {
        "id": "neutron-dhcp-agent-down",
        "title": "neutron-dhcp-agent down -- new/rebooted instances not getting an IP",
        "category": "network",
        "metric_names": [],
        "service_binaries": ["neutron-dhcp-agent"],
        "keywords": ["dhcp agent down", "no ip address", "dhcp not working", "instance no network", "didn't get an ip"],
        "what_it_means": (
            "neutron-dhcp-agent runs the dnsmasq process that hands out IPs on networks with DHCP "
            "enabled. If it's down, an instance that already has a lease (hasn't rebooted, hasn't "
            "had its lease expire) keeps working; a freshly-booted or rebooted instance on that "
            "network won't get an address at all. This only affects networks with DHCP enabled -- "
            "it has no effect on instances using purely static/cloud-init-configured addressing."
        ),
        "confirm_commands": [
            {
                "command": "openstack network agent list --agent-type dhcp",
                "description": "Alive/State/Admin State for every DHCP agent -- confirms whether it's actually down vs. just this one network's scheduling.",
                "read_only": True,
            },
            {
                "command": "openstack network agent show <agent_id>",
                "description": "Which networks this specific DHCP agent is hosting -- confirms it's actually responsible for the affected network.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 neutron_dhcp_agent",
                "description": "Common causes: can't reach Neutron's RabbitMQ/API, or a stale/conflicting dnsmasq process already bound to the network's namespace.",
                "read_only": True,
            },
            {
                "command": "ip netns exec qdhcp-<network_id> ip a",
                "description": "Confirms the DHCP network namespace and its interface actually exist on the host the agent should be running on.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart neutron_dhcp_agent",
                "description": "Standard first step -- also re-spawns dnsmasq cleanly if a stale process was the issue.",
                "read_only": False,
            },
            {
                "command": "openstack network agent set --enable <agent_id>",
                "description": "If the agent process is healthy but administratively disabled (Admin State: down), this alone restores DHCP without a restart.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/neutron.md",
    },
    {
        "id": "neutron-l3-agent-down",
        "title": "neutron-l3-agent down -- floating IPs / external connectivity broken",
        "category": "network",
        "metric_names": [],
        "service_binaries": ["neutron-l3-agent"],
        "keywords": ["l3 agent down", "floating ip not working", "can't reach instance externally", "router down", "external network unreachable"],
        "what_it_means": (
            "neutron-l3-agent implements virtual routers (via network namespaces + iptables NAT) "
            "-- floating IP SNAT/DNAT, and routing between project subnets and the external "
            "network. If it's down, floating IPs on routers it hosts stop passing traffic and "
            "east-west routing through those routers breaks, even though the instances themselves "
            "are up and reachable on their internal network. This is the single most common cause "
            "of \"the instance is running but I can't reach its floating IP.\""
        ),
        "confirm_commands": [
            {
                "command": "openstack network agent list --agent-type l3",
                "description": "Alive/State/Admin State for every L3 agent host.",
                "read_only": True,
            },
            {
                "command": "openstack router show <router_id>",
                "description": "Confirms the router is actually hosted (has an agent assigned) and its ha_state/status.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 neutron_l3_agent",
                "description": "Common causes: RabbitMQ/API connectivity, or a namespace/iptables setup failure on the agent's host.",
                "read_only": True,
            },
            {
                "command": "ip netns exec qrouter-<router_id> ip a",
                "description": "Confirms the router's namespace and its gateway/floating-IP interfaces actually exist on the host.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart neutron_l3_agent",
                "description": "Standard first step for a hung agent or a corrupted namespace state.",
                "read_only": False,
            },
            {
                "command": "openstack network agent set --enable <agent_id>",
                "description": "If administratively disabled rather than crashed, this restores routing without a restart.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/neutron.md",
    },
    {
        "id": "neutron-ovs-agent-down",
        "title": "neutron-openvswitch-agent down on a compute node -- guest network connectivity broken",
        "category": "network",
        "metric_names": [],
        "service_binaries": ["neutron-openvswitch-agent"],
        "keywords": ["ovs agent down", "open vswitch agent", "port binding failed", "instance no connectivity", "network unreachable on compute"],
        "what_it_means": (
            "neutron-openvswitch-agent wires each guest's tap interface into the OVS bridges "
            "(br-int/br-tun/br-provider) and programs the flows for VLAN/VXLAN tagging, security "
            "groups, and (if configured) DVR. If it's down on a compute node, instances already "
            "running there keep their existing flows (OVS itself doesn't stop forwarding just "
            "because the agent died), but a newly-spawned instance's port never gets bound/wired, "
            "and any *change* to security groups or network config on that host won't take effect."
        ),
        "confirm_commands": [
            {
                "command": "openstack network agent list --agent-type open-vswitch",
                "description": "Alive/State for the OVS agent on every compute node.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 neutron_openvswitch_agent",
                "description": "Common causes: lost connection to the OVS database (ovsdb-server), or RabbitMQ connectivity.",
                "read_only": True,
            },
            {
                "command": "ovs-vsctl show   # on the compute node itself",
                "description": "Confirms the bridges and port bindings actually exist independently of what the agent last reported.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart neutron_openvswitch_agent",
                "description": "Standard first step -- re-establishes the ovsdb connection and re-syncs port bindings/flows.",
                "read_only": False,
            },
            {
                "command": "docker restart openvswitch_vswitchd openvswitch_db",
                "description": "If ovs-vsctl itself is unresponsive (not just the agent), the underlying OVS daemons may need restarting first.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/neutron.md",
    },
    {
        "id": "node-unreachable",
        "title": "A whole node has stopped responding (Prometheus health cross-check)",
        "category": "host",
        # "host_down" is a sentinel set by openstack_expert.py's
        # monitoring-evidence extraction when a live read's status != "up"
        # -- not a real Prometheus/anomaly_detector metric name, just this
        # catalog's way of letting "the host itself is down" hit the
        # matcher the same uniform way every other signal does.
        "metric_names": ["host_down"],
        "service_binaries": [],
        "keywords": ["node down", "node unreachable", "host down", "host unreachable", "prometheus not scraping"],
        "what_it_means": (
            "This is Cortex's own Prometheus cross-check (ADR-0003): `Node.health` goes `down` "
            "when `up{job=\"node_exporter\"}` reports 0 for that host -- the target is known and "
            "being scraped, but the scrape itself is failing, meaning the host stopped answering "
            "on the network, not just \"OpenStack hasn't heard from it in a while.\" Every "
            "`:Service` that RUNS_ON a down Node reconciles to `unreachable` even if that "
            "service's own OpenStack-reported state still says `up` -- that reconciliation is "
            "exactly the signal that's more trustworthy than Nova/Cinder/Neutron's own service "
            "table, which can lag by its report interval."
        ),
        "confirm_commands": [
            {
                "command": "ping <hostname/ip>  and  ssh <hostname>",
                "description": "Confirms whether this is a full outage (host unreachable at the network level) or just node_exporter itself having died on an otherwise-up host.",
                "read_only": True,
            },
            {
                "command": "curl -sf http://<node_ip>:9100/metrics | head",
                "description": "If the host answers ping/ssh but this fails, node_exporter specifically has died or is firewalled -- narrower and easier to fix than a full outage.",
                "read_only": True,
            },
            {
                "command": "openstack compute service list  /  openstack volume service list  /  openstack network agent list",
                "description": "Cross-check what OpenStack's own service tables say about this host -- a real gap here (still shows 'up') versus Cortex's reconciled 'unreachable' confirms the cross-check caught something OpenStack's own polling hasn't yet.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "systemctl restart node_exporter  # on the host itself, if reachable via SSH but not scraping",
                "description": "If the host is up and reachable but node_exporter specifically died, this alone restores Prometheus visibility with no impact on OpenStack services.",
                "read_only": False,
            },
            {
                "command": "openstack compute service set --disable <host> nova-compute  (compute nodes only, once confirmed unreachable)",
                "description": "Stop new scheduling onto a confirmed-unreachable compute host while it's investigated/repaired -- doesn't affect guests already running there.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/architecture/adr-0003-prometheus-cross-check.md",
    },
    # ------------------------------------------------------------------
    # 11-15: common OpenStack infra problems with no Cortex detector yet,
    # but exactly what an operator asks "how do I check X" about
    # ------------------------------------------------------------------
    {
        "id": "rabbitmq-issue",
        "title": "RabbitMQ down or unhealthy -- cascading service failures",
        "category": "message-bus",
        "metric_names": [],
        "service_binaries": [],
        "keywords": ["rabbitmq", "amqp connection refused", "message bus down", "rabbitmq down", "queue full"],
        "what_it_means": (
            "Every OpenStack service in this deployment (Nova, Neutron, Cinder) talks to its own "
            "agents/workers over RabbitMQ, not directly -- if RabbitMQ is down or unreachable, "
            "symptoms show up as a wave of *unrelated-looking* failures across multiple services "
            "at once (nova-compute, neutron agents, and cinder-volume all logging AMQP connection "
            "errors around the same time) rather than one clean root cause. Seeing several "
            "services fail simultaneously, all logging connection-refused/timeout to the same "
            "message-bus host, is the actual signature -- treat RabbitMQ as the first thing to "
            "check whenever an incident spans more than one OpenStack service."
        ),
        "confirm_commands": [
            {
                "command": "docker exec rabbitmq rabbitmqctl cluster_status",
                "description": "Confirms the node itself is up and, in a clustered deployment, whether all cluster members see each other.",
                "read_only": True,
            },
            {
                "command": "docker exec rabbitmq rabbitmqctl list_queues name messages consumers",
                "description": "A queue with a huge backlog and zero consumers means something downstream (a specific agent) stopped consuming, not that RabbitMQ itself is broken.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 rabbitmq",
                "description": "Disk-alarm or memory-alarm messages are the most common self-inflicted RabbitMQ outage (it blocks publishers when its own resource watermarks are hit).",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart rabbitmq",
                "description": "CAUTION: every OpenStack service reconnects when RabbitMQ comes back, which briefly amplifies load -- confirm via logs this is actually needed before restarting a shared dependency this central.",
                "read_only": False,
            },
            {
                "command": "docker exec rabbitmq rabbitmqctl set_disk_free_limit <value>",
                "description": "If logs show a disk-alarm block, freeing disk space (see the host-disk-pressure entry) and/or adjusting the limit clears the publisher block without a restart.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-catalog.md",
    },
    {
        "id": "mariadb-issue",
        "title": "MariaDB down or unreachable -- API calls failing with DB errors",
        "category": "database",
        "metric_names": [],
        "service_binaries": [],
        "keywords": ["mariadb", "mysql connection", "database down", "db connection refused", "database error"],
        "what_it_means": (
            "Every OpenStack API service (nova-api, neutron-server, cinder-api, keystone, "
            "glance-api) reads/writes its state to MariaDB. If it's down or unreachable, API "
            "calls fail immediately with a database connection error rather than timing out slowly "
            "-- this is usually fast and obvious in the logs, unlike RabbitMQ issues which can "
            "manifest as slow/hung operations instead."
        ),
        "confirm_commands": [
            {
                "command": "docker exec mariadb mysqladmin ping -u root -p",
                "description": "Simplest direct confirmation the database process itself is up and accepting connections.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 mariadb",
                "description": "Common causes: disk full on the DB volume (mariadb refuses writes), or a crashed/OOM-killed process.",
                "read_only": True,
            },
            {
                "command": "docker exec mariadb mysql -u root -p -e \"SHOW PROCESSLIST;\"",
                "description": "A processlist full of long-running/locked queries (rather than the DB being down outright) points to a query or lock problem instead of an outage.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart mariadb",
                "description": "CAUTION: every OpenStack API service will briefly fail/reconnect -- confirm via logs the process is actually unhealthy (not just under heavy query load) before restarting.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-catalog.md",
    },
    {
        "id": "keystone-auth-issue",
        "title": "Authentication/token failures across OpenStack services",
        "category": "identity",
        "metric_names": [],
        "service_binaries": [],
        "keywords": ["keystone", "authentication failed", "token expired", "unauthorized", "401", "unable to establish connection to keystone"],
        "what_it_means": (
            "Every OpenStack API call is authenticated against Keystone first -- a failure here "
            "shows up as every *other* service simultaneously rejecting requests with "
            "401/\"unable to establish connection to keystone\", which looks like several services "
            "failing at once but traces back to one identity service being down, misconfigured, "
            "or (for a specific user/service account) simply having an expired/revoked token or "
            "credential."
        ),
        "confirm_commands": [
            {
                "command": "docker logs --tail 200 keystone",
                "description": "Keystone's own error -- distinguishes a real outage (can't reach its DB) from an auth/policy rejection for a specific request.",
                "read_only": True,
            },
            {
                "command": "openstack service list",
                "description": "If this succeeds, Keystone itself is reachable and your own credentials are valid -- narrows the problem to a different account/service.",
                "read_only": True,
            },
            {
                "command": "docker ps -a --filter name=keystone",
                "description": "Confirms the container itself is up, not restarting/exited.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart keystone",
                "description": "Standard first step for a hung or crash-looping Keystone container, once logs rule out a DB/config issue a restart won't fix.",
                "read_only": False,
            },
            {
                "command": "openstack user set --password <new_password> <service_user>  (service accounts only, once a genuinely expired/rotated credential is confirmed)",
                "description": "If the root cause is a specific service account's stale credential rather than Keystone itself, rotating it (matched in every service's config) resolves it.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/keystone.md",
    },
    {
        "id": "libvirt-hypervisor-issue",
        "title": "Lost connection to libvirt / a guest domain crashed unexpectedly",
        "category": "hypervisor",
        "metric_names": [],
        "service_binaries": [],
        "keywords": ["libvirt", "lost connection to libvirt", "qemu crashed", "domain destroyed", "hypervisor error", "kvm error"],
        "what_it_means": (
            "nova-compute talks to libvirtd (containerized as nova_libvirt in this Kolla "
            "deployment) to create/manage/query guest domains. \"Lost connection to libvirt\" "
            "means that channel broke -- libvirtd crashed/restarted, or the socket it listens on "
            "became unavailable -- which makes nova-compute unable to report accurate instance "
            "state or perform any lifecycle operation, even though already-running guest domains "
            "keep running under qemu independently of libvirtd being up. A domain that's actually "
            "crashed (qemu itself died) is a different, more serious problem -- the guest is "
            "actually down, not just unreported."
        ),
        "confirm_commands": [
            {
                "command": "docker ps -a --filter name=nova_libvirt",
                "description": "Confirms whether the libvirt container itself is up, restarting, or exited.",
                "read_only": True,
            },
            {
                "command": "docker exec nova_libvirt virsh list --all",
                "description": "If this itself hangs or errors, libvirtd is the actual problem, independent of nova-compute. If it works, cross-check the specific domain's state (running/shut off/crashed).",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 300 nova_libvirt",
                "description": "libvirtd's own error -- common causes are a corrupted domain XML, a storage pool becoming unavailable, or the host running out of resources for a new domain.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 300 nova_compute",
                "description": "nova-compute's side of the same failure -- confirms whether it's actively retrying the connection or has given up.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "docker restart nova_libvirt",
                "description": "CAUTION: briefly disconnects nova-compute's view of every guest on this host (running guests are NOT stopped by this -- qemu processes are independent of libvirtd), then restart nova_compute afterward to re-sync.",
                "read_only": False,
            },
            {
                "command": "docker restart nova_compute  # after nova_libvirt is confirmed healthy",
                "description": "Re-establishes nova-compute's connection and re-syncs its view of instance state once libvirtd itself is healthy again.",
                "read_only": False,
            },
            {
                "command": "openstack server reboot --hard <instance_id>  (only for a domain confirmed actually crashed, not just unreported)",
                "description": "A hard reboot re-creates the domain from scratch -- appropriate once you've confirmed via `virsh list --all` that qemu itself died, not just that libvirtd temporarily lost track of it.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/nova.md",
    },
    {
        "id": "glance-image-issue",
        "title": "Instance build fails during image download/decompress",
        "category": "image",
        "metric_names": [],
        "service_binaries": [],
        "keywords": ["glance", "image download failed", "image corrupt", "qcow2 error", "image not found"],
        "what_it_means": (
            "Before nova-compute can spawn a guest, it has to fetch the image from Glance and "
            "convert/cache it locally. A failure here surfaces as an instance that fails during "
            "BUILD (never even reaches the libvirt-domain-creation step) with a fault referencing "
            "the image -- most commonly a corrupted or truncated image in the Glance backend, "
            "Glance itself being unreachable, or the compute node running out of local disk space "
            "for the image cache (see the host-disk-pressure entry above)."
        ),
        "confirm_commands": [
            {
                "command": "openstack image show <image_id>",
                "description": "Confirms the image's status is `active` (not `queued`/`killed`) and checks its recorded checksum/size.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 glance_api",
                "description": "Glance's own error if the download itself failed at the source (backend storage issue) rather than on the compute node's side.",
                "read_only": True,
            },
            {
                "command": "docker logs --tail 200 nova_compute",
                "description": "The compute-side error -- a checksum mismatch, a decompression failure, or a local disk-full error while caching the image.",
                "read_only": True,
            },
        ],
        "remediation_commands": [
            {
                "command": "openstack image save <image_id> --file /tmp/check.img && qemu-img check /tmp/check.img",
                "description": "Directly verifies the image file's integrity independent of what any service reports -- confirms whether the image itself needs re-uploading.",
                "read_only": False,
            },
            {
                "command": "openstack image delete <image_id>  (only once confirmed corrupted, followed by re-upload)",
                "description": "A genuinely corrupted image needs to be replaced -- re-upload a known-good copy under a new (or the same, after deletion) image record.",
                "read_only": False,
            },
        ],
        "doc_ref": "docs/knowledge/service-detail/glance.md",
    },
]

# Every entry's id must be unique -- the agent looks entries up by id for
# citation, and a duplicate would silently shadow an earlier entry.
assert len({e["id"] for e in CATALOG}) == len(CATALOG), "duplicate SymptomEntry id in CATALOG"
