"""Sandbox-only drift demonstration for the Security groups page.

Phase Sec-1/Sec-2 already gave `_check_sec_group_diff` real drift
detection and openstack-sim already gave the sandbox real fault-injection
endpoints for it (`/_sandbox/security-group-rule/add|remove`, see that
module's own "sandbox-only fault injection" section) -- but exercising
that end to end meant hand-running scripts/run_security_snapshot.py and a
raw curl, twice, in the right order (see that script's own docstring for
the exact steps). This module wires the same flow behind one call so the
Security groups page can offer a "simulate real drift" control instead of
a terminal walkthrough.

Gated on CORTEX_ENV=sandbox (see infra/docker-compose.sandbox.yml) the
same way ansible_runner.py's `_guard_against_sandbox_in_production` gates
sandbox-inventory writes: this mutates simulated OpenStack state, and
that must never be reachable against a real cloud.
"""
import logging
import os
import time

import requests
from sqlalchemy.orm import Session

from .. import crud
from . import security_audit
from .security_snapshot_builder import capture_security_group_snapshots

logger = logging.getLogger(__name__)

CORTEX_ENV = os.environ.get("CORTEX_ENV", "development").strip().lower()
# Reachable from the api container on the same cortex-sandbox network
# openstacksdk's clouds.yaml already points at (see infra/docker-compose
# .sandbox.yml) -- this hits openstack-sim's own sandbox-only HTTP
# surface directly, not through openstacksdk, since
# /_sandbox/security-group-rule/add|remove isn't a real Neutron API.
OPENSTACK_SIM_BASE_URL = os.environ.get("OPENSTACK_SIM_BASE_URL", "http://openstack-sim:5000")

# Deliberately NOT one of security_audit._SENSITIVE_PORTS and scoped to a
# private CIDR -- this rule should show up under Drift since last
# snapshot without also tripping the overly-permissive risky_rules
# baseline, which is exactly the "a change worth noticing even when it
# isn't itself risky" case Phase Sec-1 exists to catch (see
# security_audit.diff_security_groups's own docstring).
_DEMO_RULE_PORT = 8080
_DEMO_RULE_CIDR = "10.0.0.0/16"


class SandboxUnavailableError(Exception):
    """Raised when a sandbox-only endpoint is hit outside CORTEX_ENV=sandbox."""


class NoSecurityGroupError(Exception):
    """Raised when the target host has no security group to demo drift on."""


def require_sandbox() -> None:
    if CORTEX_ENV != "sandbox":
        raise SandboxUnavailableError(
            "Drift simulation is only available when CORTEX_ENV=sandbox "
            "(see infra/docker-compose.sandbox.yml)."
        )


def _demo_rule_id(hostname: str) -> str:
    return f"demo-drift-{hostname}-{int(time.time())}"


def inject_demo_drift(db: Session, hostname: str) -> dict:
    """Simulates one real, end-to-end security-group change on `hostname`:

    1. Takes a fresh baseline snapshot right now
       (security_snapshot_builder.capture_security_group_snapshots) --
       `drift` is only ever computed against *some* prior snapshot, so
       without a snapshot taken immediately before the injected change,
       there's nothing for the next read to diff against.
    2. Adds one new ingress rule via openstack-sim's own
       `/_sandbox/security-group-rule/add` -- the real sandbox fault-
       injection endpoint this project already ships, not a fake/mocked
       one -- to the first security group actually attached to this
       host.
    3. Re-scans this one host immediately (security_scan_cache.
       scan_one_node) so the cached finding -- and therefore GET
       /api/v1/security/groups/{hostname} -- reflects the injected drift
       right away instead of waiting for the next periodic scan tick.
    """
    require_sandbox()

    groups = security_audit.get_node_security_groups(hostname)["security_groups"]
    if not groups:
        raise NoSecurityGroupError(f"No security group attached to any instance on {hostname}.")
    target = groups[0]

    capture_security_group_snapshots(db)

    rule_id = _demo_rule_id(hostname)
    rule = {
        "id": rule_id,
        "security_group_id": target["id"],
        "direction": "ingress",
        "ethertype": "IPv4",
        "protocol": "tcp",
        "port_range_min": _DEMO_RULE_PORT,
        "port_range_max": _DEMO_RULE_PORT,
        "remote_ip_prefix": _DEMO_RULE_CIDR,
    }
    response = requests.post(
        f"{OPENSTACK_SIM_BASE_URL}/_sandbox/security-group-rule/add", json=rule, timeout=8
    )
    response.raise_for_status()

    _rescan_one_host(db, hostname)

    return {
        "hostname": hostname,
        "security_group": target["name"],
        "security_group_id": target["id"],
        "rule_id": rule_id,
        "detail": (
            f"Added a new internal-only ingress rule (tcp/{_DEMO_RULE_PORT} from {_DEMO_RULE_CIDR}) "
            f"to '{target['name']}' on {hostname}, right after taking a fresh baseline snapshot. "
            "It's now visible under Drift since last snapshot for this host."
        ),
    }


def reset_demo_drift(db: Session, hostname: str, rule_id: str) -> dict:
    """Removes one previously-injected demo rule (see `inject_demo_drift`)
    and re-scans the host so the cache drops the drift entry it created.
    Removes only the one rule by id, via `/_sandbox/security-group-rule/
    remove`, rather than calling openstack-sim's blanket `/_sandbox/fault/
    reset` -- that would also clear any router/port/floating-ip faults an
    unrelated topology demo in the same sandbox is using.
    """
    require_sandbox()
    response = requests.post(
        f"{OPENSTACK_SIM_BASE_URL}/_sandbox/security-group-rule/remove",
        json={"id": rule_id},
        timeout=8,
    )
    response.raise_for_status()

    _rescan_one_host(db, hostname)

    return {"hostname": hostname, "rule_id": rule_id, "removed": True}


def _rescan_one_host(db: Session, hostname: str) -> None:
    # Local import: security_scan_cache doesn't depend on this module, but
    # importing it at module load time would still create an import-order
    # footgun if that ever changes, since both live under the same
    # services package -- deferred the same way agents/nodes/security.py
    # defers its own DB-session helper imports.
    from . import security_scan_cache

    node_row = crud.get_node_by_hostname(db, hostname)
    if node_row is not None:
        security_scan_cache.scan_one_node(db, node_row)
