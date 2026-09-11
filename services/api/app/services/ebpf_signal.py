"""Kernel-level security signal for the Security Agent's eBPF-signal
sub-check (v0.9, agents/nodes/security.py) -- process/syscall-level alerts
from a Falco- or Tetragon-style sensor, the hardest of the four sub-checks
to fake or miss since it observes actual kernel events rather than a log
line an attacker could avoid writing or a package-version snapshot that
only tells you what's *installed*, not what's *running*. That's also
exactly why the Security Agent's confidence weighting favors this signal
over the others (see nodes/security.py's module docstring).

Same shape as loki_client.py/network_health.py: a thin HTTP client against
a real, configurable endpoint (`CORTEX_EBPF_ALERTS_URL`), no mocking or
simulated data baked in here -- point it at a real Falco `/alerts` HTTP
output plugin or a Tetragon gRPC-to-HTTP bridge and this reads real
kernel-level events. In an environment with no such sensor deployed yet
(the default for a fresh openstack-sim checkout), every call here fails
with a connection error, which the caller (nodes/security.py, wrapped in
resilience.get_breaker same as every other external read in this
codebase) turns into a `degraded: True` sub-check rather than a crash --
"we don't have a kernel-level sensor to ask" is a real, common state, not
an error condition unique to this module.

Expected response shape from `{CORTEX_EBPF_ALERTS_URL}/alerts?host=<hostname>`:
    [{"rule": str, "priority": "critical"|"warning"|"notice"|"info",
      "output": str, "time": ISO-8601 str}, ...]
one entry per fired rule, most-recent-relevant first -- the same shape
Falco's own JSON output uses for `rule`/`priority`/`output`/`time`, so a
real Falco deployment's HTTP output plugin can be pointed at directly
without a translation layer.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

EBPF_ALERTS_URL = os.environ.get("CORTEX_EBPF_ALERTS_URL", "http://tetragon-bridge:9110")

# Falco/Tetragon priority strings, ranked so the worst alert for a host is
# easy to pick out without a bespoke severity table per sensor vendor.
_PRIORITY_RANK = {"emergency": 5, "alert": 5, "critical": 4, "error": 3, "warning": 2, "notice": 1, "informational": 0, "info": 0}


def get_node_ebpf_alerts(hostname: str) -> list[dict]:
    """Every currently-active kernel-level alert for this host, most
    severe first. Raises on a connection/timeout/non-2xx -- the caller
    (nodes/security.py's `_check_ebpf_signal`) is what wraps this in a
    breaker and degrades gracefully, same division of responsibility as
    every other client function in this codebase (loki_client.query_range,
    network_health.get_node_network_health): the client raises real
    errors, the agent node decides how to degrade."""
    response = requests.get(f"{EBPF_ALERTS_URL}/alerts", params={"host": hostname}, timeout=8)
    response.raise_for_status()
    alerts = response.json()
    return sorted(alerts, key=lambda a: _PRIORITY_RANK.get((a.get("priority") or "").lower(), 0), reverse=True)


def list_hosts_with_alerts() -> list[str]:
    """Every hostname with at least one currently-active alert -- the bulk
    read agents/nodes/anomaly.py's `_incident_scope_from_living_model`
    needs to fold a live eBPF signal into a broad "is anything wrong"
    question's incident scope without a per-node round trip (mirrors
    network_health.list_hosts_with_down_agents's same reasoning)."""
    response = requests.get(f"{EBPF_ALERTS_URL}/alerts", timeout=8)
    response.raise_for_status()
    alerts = response.json()
    hosts = {a.get("host") for a in alerts if a.get("host")}
    return sorted(hosts)
