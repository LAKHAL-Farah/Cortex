"""Package-version -> known-CVE matching for the Security Agent's
CVE-match sub-check (v0.9, agents/nodes/security.py).

Two independent pieces, deliberately kept separate:

1. `get_package_inventory(hostname)` -- an HTTP client, same
   configurable-real-endpoint shape as ebpf_signal.py/loki_client.py, this
   time against `CORTEX_PACKAGE_INVENTORY_URL`. As of Phase Sec-3, the
   sandbox (infra/docker-compose.sandbox.yml) points this at openstack-sim's
   own `/packages?host=` route -- seeded with a real known-vulnerable
   openssh-server version on controller-sim and clean versions elsewhere,
   so `_check_cve_match` returns a genuine matched/clean finding instead of
   degrading, without a fifth sandbox container. Outside the sandbox,
   nothing production-shaped collects a per-node package inventory yet --
   point CORTEX_PACKAGE_INVENTORY_URL at a real fact-gathering source (an
   Ansible-facts scraper, an osquery/Fleet endpoint, a Prometheus
   node_exporter textfile collector publishing dpkg/rpm versions -- any of
   these would work) and this starts reading real installed versions there
   too. A host with no collector reachable still fails with a connection
   error, which the caller (same as ebpf_signal.py) degrades into "no
   signal, unknown" rather than a crash.
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
    if ":" in version.split("-", 1)[0]:
        # dpkg epoch ("1:8.9p1-3ubuntu0.10") -- drop it, it is not part of the upstream number.
        version = version.split(":", 1)[1]
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


def _is_distro_patched(version: str) -> bool:
    """Ubuntu/Debian back-port security fixes without changing the upstream number
    (sudo 1.9.9-1ubuntu2.4 already carries fixes "fixed upstream in 1.9.12"), so comparing a
    distro build against an upstream fixed_version produces false positives. Those packages
    are judged by the distribution's own pending security updates instead (see below)."""
    return "ubuntu" in version or "deb" in version


def match_cves(packages: list[dict]) -> list[dict]:
    """Pure -- no network calls. Returns one entry per (installed package,
    known CVE) pair, each carrying both the CVE's own fields and the
    installed version that's vulnerable, so the caller doesn't have to zip
    the two lists back together to narrate a finding.

    Two sources:
    1. `_CVE_DATABASE` -- installed version older than the CVE's upstream
       fixed_version. Skipped for distro-patched builds (see `_is_distro_patched`).
    2. `security_update` on a package entry -- the host's own package manager says a
       *-security update is pending for it (the real collector, infra/collector, sets
       this from `apt list --upgradable` and adds the CVE ids from the changelog).
       This is authoritative for Ubuntu/Debian hosts."""
    matches: list[dict] = []
    for pkg in packages:
        name = pkg.get("name")
        version = pkg.get("version")
        if not name or not version:
            continue

        update = pkg.get("security_update")
        if update:
            known = {e["cve_id"]: e for entries in _CVE_DATABASE.values() for e in entries}
            cves = update.get("cves") or []
            fixed = update.get("version") or "a newer version"
            for cve_id in cves or ["PENDING-SECURITY-UPDATE"]:
                ref = known.get(cve_id, {})
                matches.append({
                    "cve_id": cve_id,
                    "severity": ref.get("severity", "high"),
                    "fixed_version": fixed,
                    "description": ref.get("description")
                    or f"A security update for {name} is available from the distribution's security pocket.",
                    "package": name,
                    "installed_version": version,
                })
            continue  # the distribution's answer wins over the upstream table for this package

        if name not in _CVE_DATABASE or _is_distro_patched(version):
            continue
        for entry in _CVE_DATABASE[name]:
            if _version_lt(version, entry["fixed_version"]):
                matches.append({**entry, "package": name, "installed_version": version})
    return matches
