"""Turns a raw `trivy rootfs --format json` scan into a few dozen rows the Security page can show.

A raw Ubuntu scan has tens of thousands of findings (one per CVE per binary package, and Ubuntu kernels
alone are ~9,000 CVEs across a dozen linux-* packages). This keeps it useful:

  * non-kernel packages: one row per package, with its CVE count, severity breakdown and the worst CVE
    (unfixed CVEs are kept: they are the ones apt cannot show);
  * kernels: one row per kernel RELEASE (running vs left-over), counting only CVEs that a newer kernel
    already fixes. Unfixed kernel CVEs are the normal state of any Ubuntu kernel and would only be noise.

Runs on the monitored node (Python 3.10, standard library only) so the collector never has to pull a
60 MB JSON over SSH:   python3 trivy_aggregate.py <scan.json> <MIN_SEVERITY> <uname -r>
"""
import json
import re
import sys

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
MAX_ROWS = 60


def _rank(severity):
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def kernel_release(pkg_name, installed_version):
    """'6.8.0-40' from linux-headers-6.8.0-40-generic / version 6.8.0-40.40~22.04.3; 'meta' for unversioned metapackages."""
    match = re.match(r"^(\d+\.\d+\.\d+-\d+)[.~+]", installed_version or "") or re.search(r"(\d+\.\d+\.\d+-\d+)", pkg_name or "")
    return match.group(1) if match else "meta"


def _bucket():
    return {"cves": {}}  # id -> (rank, severity, fixed_version or "")


def _add(bucket, vuln):
    cve_id = vuln.get("VulnerabilityID")
    severity = vuln.get("Severity", "UNKNOWN")
    entry = (_rank(severity), severity, vuln.get("FixedVersion") or "")
    previous = bucket["cves"].get(cve_id)
    # keep the most informative sighting of this CVE (best severity, and prefer one that has a fix)
    if previous is None or (entry[0], not entry[2]) < (previous[0], not previous[2]):
        bucket["cves"][cve_id] = entry


def _summarise(bucket):
    cves = bucket["cves"]
    if not cves:
        return None
    by_severity = {}
    for _rank_value, severity, _fixed in cves.values():
        by_severity[severity] = by_severity.get(severity, 0) + 1
    worst_id, (_r, worst_sev, worst_fix) = min(cves.items(), key=lambda kv: (kv[1][0], not kv[1][2], kv[0]))
    ordered = sorted(cves.items(), key=lambda kv: (kv[1][0], kv[0]))
    return {
        "count": len(cves),
        "fixable": sum(1 for v in cves.values() if v[2]),
        "by_severity": by_severity,
        "worst": {"id": worst_id, "severity": worst_sev, "fixed": worst_fix},
        "examples": [cve_id for cve_id, _ in ordered[:4]],
    }


def aggregate(data, uname, min_severity="MEDIUM"):
    floor = _rank(min_severity)
    running_match = re.match(r"(\d+\.\d+\.\d+-\d+)", uname or "")
    running = running_match.group(1) if running_match else None
    packages, kernels, total = {}, {}, 0

    for result in data.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            total += 1
            if _rank(vuln.get("Severity", "UNKNOWN")) > floor:
                continue
            name = vuln.get("PkgName") or ""
            if name.startswith("linux-"):
                if not vuln.get("FixedVersion"):
                    continue  # unfixed kernel CVE: informational only, see module docstring
                release = kernel_release(name, vuln.get("InstalledVersion"))
                _add(kernels.setdefault(release, _bucket()), vuln)
            else:
                _add(packages.setdefault((name, vuln.get("InstalledVersion") or ""), _bucket()), vuln)

    package_rows = []
    for (name, version), bucket in packages.items():
        summary = _summarise(bucket)
        if summary:
            package_rows.append(dict(summary, name=name, version=version))
    package_rows.sort(key=lambda r: (_rank(r["worst"]["severity"]), -r["count"], r["name"]))

    kernel_rows = []
    for release, bucket in kernels.items():
        summary = _summarise(bucket)
        if summary:
            kernel_rows.append(dict(summary, release=release, running=(release == running)))
    kernel_rows.sort(key=lambda r: (not r["running"], r["release"]))

    return {"total_findings": total, "min_severity": min_severity, "running_kernel": running,
            "packages": package_rows[:MAX_ROWS], "kernels": kernel_rows}


if __name__ == "__main__":
    path, min_sev, uname = sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""
    with open(path) as handle:
        print(json.dumps(aggregate(json.load(handle), uname, min_sev)))
