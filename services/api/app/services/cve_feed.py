"""Package-version -> known-CVE matching for the Security Agent's
CVE-match sub-check (v0.9, agents/nodes/security.py).

Two independent pieces, deliberately kept separate:

1. `get_package_inventory(hostname)` -- an HTTP client, same
   configurable-real-endpoint shape as ebpf_signal.py/loki_client.py, this
   time against `CORTEX_PACKAGE_INVENTORY_URL`. Nothing in this codebase
   collects a per-node package inventory yet (see security.py's module
   docstring for the honest state of that), so in a fresh openstack-sim
   checkout this call fails with a connection error -- same as
   ebpf_signal.py, the caller degrades that into "no signal, unknown"
   rather than treating an absent collector as a crash. Point
   CORTEX_PACKAGE_INVENTORY_URL at a real fact-gathering source (an
   Ansible-facts scraper, an osquery/Fleet endpoint, a Prometheus
   node_exporter textfile collector publishing dpkg/rpm versions -- any of
   these would work) and this starts reading real installed versions.
2. `match_cves(packages)` -- a pure function, no network calls, matching
   an already-fetched package list against `_CVE_DATABASE` below. Kept
   pure and separate from the fetch specifically so it's trivially unit-
   testable without mocking HTTP, and so a real CVE feed (NVD's API, a
   vendor OVAL feed, grype/trivy's local DB) can be swapped in behind it
   later without touching the matching logic itself.

`_CVE_DATABASE` is a small, hand-picked, illustrative reference table --
real CVE IDs and real affected-version ranges for a handful of the
packages an OpenStack control-plane host commonly runs, in the same spirit
as openstack_expert_catalog.py's embedded runbook catalog (real, useful,
deliberately finite rather than a live feed integration). Extend it, or
swap `match_cves` to call a real feed, as this environment's needs grow --
see that catalog's own module docstring for the same tradeoff made there.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

PACKAGE_INVENTORY_URL = os.environ.get("CORTEX_PACKAGE_INVENTORY_URL", "http://package-inventory:9111")


def _version_tuple(version: str) -> tuple[int, ...]:
    """"2.4.10" -> (2, 4, 10). Non-numeric trailing suffixes (~ubuntu1,
    -1+deb12u1, etc, common in real dpkg/rpm version strings) are dropped
    at the first non-digit segment rather than raising -- good enough for
    "is this older than the fixed version", not a full Debian
    version-comparison algorithm (python-apt's own, for a real dpkg
    inventory, would be the correct tool for that edge case)."""
    parts: list[int] = []
    for chunk in version.replace("-", ".").replace("~", ".").replace("+", ".").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _version_lt(a: str, b: str) -> bool:
    return _version_tuple(a) < _version_tuple(b)


# Illustrative, hand-picked reference entries -- see module docstring.
# package name -> list of {cve_id, severity, fixed_version, description}.
# A package is flagged when its installed version is older than
# fixed_version (see match_cves).
_CVE_DATABASE: dict[str, list[dict]] = {
    "openssh-server": [
        {
            "cve_id": "CVE-2024-6387",
            "severity": "critical",
            "fixed_version": "9.8p1",
            "description": "regreSSHion -- signal handler race condition allowing unauthenticated remote code execution as root.",
        },
    ],
    "openssl": [
        {
            "cve_id": "CVE-2022-3602",
            "severity": "high",
            "fixed_version": "3.0.7",
            "description": "X.509 punycode buffer overflow, reachable via a malicious certificate during TLS handshake.",
        },
    ],
    "sudo": [
        {
            "cve_id": "CVE-2023-22809",
            "severity": "high",
            "fixed_version": "1.9.12p2",
            "description": "sudoedit privilege escalation via crafted EDITOR/VISUAL environment variable.",
        },
    ],
    "runc": [
        {
            "cve_id": "CVE-2024-21626",
            "severity": "high",
            "fixed_version": "1.1.12",
            "description": "container escape via a leaked file descriptor, allowing host filesystem access from inside a container.",
        },
    ],
    "libvirt": [
        {
            "cve_id": "CVE-2024-2494",
            "severity": "medium",
            "fixed_version": "10.1.0",
            "description": "storage-pool refresh race condition allowing local denial of service against the libvirt daemon.",
        },
    ],
}


def get_package_inventory(hostname: str) -> list[dict]:
    """[{"name": str, "version": str}, ...] for every package this host
    reports installed. Raises on a connection/timeout/non-2xx -- see
    module docstring on why that's the expected, gracefully-degraded-by-
    the-caller state in an environment with no inventory collector wired
    up yet."""
    response = requests.get(f"{PACKAGE_INVENTORY_URL}/packages", params={"host": hostname}, timeout=8)
    response.raise_for_status()
    return response.json()


def match_cves(packages: list[dict]) -> list[dict]:
    """Pure -- no network calls. Returns one entry per (installed package,
    known CVE) pair where the installed version is older than the CVE's
    fixed_version, each carrying both the CVE's own fields and the
    installed version that's vulnerable, so the caller doesn't have to zip
    the two lists back together to narrate a finding."""
    matches: list[dict] = []
    for pkg in packages:
        name = pkg.get("name")
        version = pkg.get("version")
        if not name or not version or name not in _CVE_DATABASE:
            continue
        for entry in _CVE_DATABASE[name]:
            if _version_lt(version, entry["fixed_version"]):
                matches.append({**entry, "package": name, "installed_version": version})
    return matches
