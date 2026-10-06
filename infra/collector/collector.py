"""Cortex security collector.

The Cortex API's security checks read four "collector" HTTP endpoints that only
exist in the sandbox (openstack-sim, tetragon-bridge-sim). This service is the
real-infrastructure version of them, serving the exact shapes the API already
expects, so the API code does not change:

  GET  /packages?host=<name>            -> [{"name","version","security_update"?}]
  GET  /listening-ports?host=<name>     -> [{"port","protocol","process"}]
  GET  /alerts[?host=<name>]            -> [{"rule","priority","output","time","host"}]
  POST /falco?token=...                 <- Falco http_output (JSON), stored in memory
  GET  /_sandbox/keystone/token-log     -> [{"username","project","source_ip",
                                             "issued_at","expires_at"}]
       (the odd path is what keystone_audit.py requests; kept for compatibility)

Node data is read over SSH (same key and inventory the Ansible runner uses).
A source that cannot be read answers 503, which the API turns into a degraded
sub-check ("unknown") rather than a false "all clear".
"""
import contextlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

import paramiko
from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger("collector")
logging.basicConfig(level=logging.INFO)

INVENTORY_PATH = os.environ.get("COLLECTOR_INVENTORY", "/infra/ansible/inventory/hosts.ini")
SSH_KEY_DIR = os.environ.get("COLLECTOR_SSH_KEY_DIR", "/root/.ssh")
SSH_USER_DEFAULT = os.environ.get("COLLECTOR_SSH_USER", "root")
SSH_TIMEOUT = float(os.environ.get("COLLECTOR_SSH_TIMEOUT", "15"))
COMMAND_TIMEOUT = float(os.environ.get("COLLECTOR_COMMAND_TIMEOUT", "180"))
# How often every host is re-read in the background. Requests never wait for SSH.
REFRESH_SECONDS = float(os.environ.get("COLLECTOR_REFRESH_SECONDS", "900"))
# Data older than this is treated as missing (503) so a dead refresh cannot look healthy.
MAX_STALE_SECONDS = float(os.environ.get("COLLECTOR_MAX_STALE_SECONDS", "21600"))
DATA_DIR = os.environ.get("COLLECTOR_DATA_DIR", "/data")
ALERT_IGNORE_REGEX = os.environ.get("COLLECTOR_ALERT_IGNORE_REGEX", "")
BACKGROUND = os.environ.get("COLLECTOR_DISABLE_BACKGROUND", "") == ""
MAX_CHANGELOG_PACKAGES = int(os.environ.get("COLLECTOR_MAX_CHANGELOG_PACKAGES", "40"))
FALCO_TOKEN = os.environ.get("COLLECTOR_FALCO_TOKEN", "")
FALCO_UNIT = os.environ.get("COLLECTOR_FALCO_UNIT", "falco-modern-bpf")
ALERT_MIN_PRIORITY = os.environ.get("COLLECTOR_ALERT_MIN_PRIORITY", "warning").lower()
ALERT_TTL_SECONDS = float(os.environ.get("COLLECTOR_ALERT_TTL_SECONDS", "3600"))
KEYSTONE_HOST = os.environ.get("COLLECTOR_KEYSTONE_HOST", "controller")
KEYSTONE_LOG_PATH = os.environ.get(
    "COLLECTOR_KEYSTONE_LOG_PATH", "/var/log/kolla/keystone/keystone-apache-public-access.log"
)
KEYSTONE_CONTAINER = os.environ.get("COLLECTOR_KEYSTONE_CONTAINER", "keystone")
KEYSTONE_LOG_LINES = int(os.environ.get("COLLECTOR_KEYSTONE_LOG_LINES", "3000"))
KEYSTONE_TOKEN_TTL = int(os.environ.get("COLLECTOR_KEYSTONE_TOKEN_TTL_SECONDS", "3600"))
KEYSTONE_IGNORE_IPS = {
    ip.strip() for ip in os.environ.get("COLLECTOR_KEYSTONE_IGNORE_SOURCE_IPS", "").split(",") if ip.strip()
}

PRIORITY_RANK = {"emergency": 5, "alert": 5, "critical": 4, "error": 3, "warning": 2, "notice": 1,
                 "informational": 0, "info": 0, "debug": 0}



# --------------------------------------------------------------------------
# Background refresh
# --------------------------------------------------------------------------
def refresh_host(host: str) -> dict:
    """Re-reads one host. Each source is independent: one failing keeps the other's data."""
    done = {}
    for name, fn in (("packages", lambda: store_put(("packages", host), _collect_packages(host))),
                     ("ports", lambda: _store_ports(host))):
        try:
            fn()
            done[name] = "ok"
        except HTTPException as exc:
            done[name] = f"failed: {exc.detail}"
            logger.warning("refresh %s/%s: %s", host, name, exc.detail)
        except Exception as exc:  # never let the loop die
            done[name] = f"failed: {type(exc).__name__}: {exc}"
            logger.exception("refresh %s/%s crashed", host, name)
    return done


def _store_ports(host: str) -> None:
    ports, sensor_active = _collect_ports_and_sensor(host)
    store_put(("ports", host), ports)
    store_put(("sensor", host), sensor_active)


def refresh_all() -> None:
    if not _refresh_lock.acquire(blocking=False):
        return  # a refresh is already running
    try:
        hosts = sorted(load_inventory())
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=max(1, min(4, len(hosts)))) as pool:
            list(pool.map(refresh_host, hosts))
        logger.info("refreshed %d hosts in %.0fs", len(hosts), time.monotonic() - started)
    except HTTPException as exc:
        logger.warning("refresh skipped: %s", exc.detail)
    finally:
        _refresh_lock.release()


def _refresh_loop() -> None:
    while True:
        refresh_all()
        time.sleep(REFRESH_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(_app):
    _load_alerts()
    if BACKGROUND:
        threading.Thread(target=_refresh_loop, name="collector-refresh", daemon=True).start()
    yield


app = FastAPI(title="Cortex security collector", lifespan=lifespan)


# --------------------------------------------------------------------------
# Inventory + SSH
# --------------------------------------------------------------------------
def load_inventory(path: str | None = None) -> dict[str, dict]:
    """{hostname: {"ip": str, "user": str}} from the Ansible hosts.ini."""
    hosts: dict[str, dict] = {}
    try:
        with open(path or INVENTORY_PATH) as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise HTTPException(503, f"inventory not readable: {exc}") from exc
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "[", ";")):
            continue
        parts = line.split()
        attrs = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        ip = attrs.get("ansible_host")
        if ip:
            hosts[parts[0]] = {"ip": ip, "user": attrs.get("ansible_user", SSH_USER_DEFAULT)}
    return hosts


def _resolve(host: str) -> dict:
    hosts = load_inventory()
    if host not in hosts:
        raise HTTPException(404, f"unknown host {host!r}; known: {', '.join(sorted(hosts)) or 'none'}")
    return hosts[host]


def _key_files() -> list[str]:
    names = ("id_ed25519", "id_ecdsa", "id_rsa")
    return [os.path.join(SSH_KEY_DIR, n) for n in names if os.path.isfile(os.path.join(SSH_KEY_DIR, n))]


def ssh_run(host: str, command: str, timeout: float | None = None) -> tuple[int, str]:
    """Runs `command` on an inventory host. Returns (exit_status, stdout)."""
    target = _resolve(host)
    keys = _key_files()
    if not keys:
        raise HTTPException(503, f"no SSH key found in {SSH_KEY_DIR} (expected id_ed25519/id_ecdsa/id_rsa)")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # same as ansible.cfg host_key_checking=False
    try:
        client.connect(target["ip"], username=target["user"], key_filename=keys, timeout=SSH_TIMEOUT,
                       banner_timeout=SSH_TIMEOUT, auth_timeout=SSH_TIMEOUT,
                       look_for_keys=False, allow_agent=False)
        _, stdout, _ = client.exec_command(command, timeout=timeout or COMMAND_TIMEOUT)
        out = stdout.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out
    except Exception as exc:  # network, auth, timeout
        raise HTTPException(503, f"ssh to {host} failed: {type(exc).__name__}: {exc}") from exc
    finally:
        client.close()


# Tests replace this.
RUNNER: Callable[[str, str], tuple[int, str]] = ssh_run

# Per-host data is refreshed by a background thread and *read* by the endpoints, so an API
# request never waits for SSH / apt (the API times out after 8 s and its circuit breaker opens).
_store: dict[tuple, tuple[float, object]] = {}
_store_lock = threading.Lock()
_refresh_lock = threading.Lock()


def store_put(key: tuple, value: object) -> None:
    with _store_lock:
        _store[key] = (time.monotonic(), value)


def store_get(key: tuple):
    """Fresh-or-stale value, or 503 when it was never collected / is older than MAX_STALE_SECONDS."""
    with _store_lock:
        hit = _store.get(key)
    if hit is None:
        raise HTTPException(503, f"{key[0]} for {key[1] if len(key) > 1 else ''} not collected yet "
                                 f"(background refresh running; retry in a minute)")
    age = time.monotonic() - hit[0]
    if age > MAX_STALE_SECONDS:
        raise HTTPException(503, f"{key[0]} data is {int(age)}s old (refresh keeps failing); check collector logs")
    return hit[1]


def cached(key: tuple, ttl: float, producer: Callable[[], object]):
    """Small synchronous TTL cache, only for cheap reads (Keystone log)."""
    now = time.monotonic()
    with _store_lock:
        hit = _store.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    store_put(key, value)
    return value


# --------------------------------------------------------------------------
# Packages + pending security updates
# --------------------------------------------------------------------------
_CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}")
_UPGRADABLE_RE = re.compile(r"^(?P<name>[^/\s]+)/(?P<pockets>\S+)\s+(?P<new>\S+)\s+\S+\s+\[upgradable from: (?P<old>[^\]]+)\]")


def parse_dpkg(output: str) -> list[dict]:
    """`dpkg-query -W -f='${Package}\\t${Version}\\n'` -> [{"name","version"}]."""
    packages = []
    for line in output.splitlines():
        name, _, version = line.partition("\t")
        if name and version:
            packages.append({"name": name.strip(), "version": version.strip()})
    return packages


def parse_security_upgradable(output: str) -> dict[str, dict]:
    """`apt list --upgradable` lines whose pocket is a *-security one.
    Example: sudo/jammy-updates,jammy-security 1.9.9-1ubuntu2.5 amd64 [upgradable from: 1.9.9-1ubuntu2.4]"""
    found: dict[str, dict] = {}
    for line in output.splitlines():
        m = _UPGRADABLE_RE.match(line.strip())
        if m and "-security" in m.group("pockets"):
            found[m.group("name")] = {"version": m.group("new"), "installed": m.group("old")}
    return found


def parse_changelog_cves(changelog: str, installed_version: str) -> list[str]:
    """CVE ids mentioned in the changelog entries newer than `installed_version`.

    The changelog is in dpkg format: each entry starts with `pkg (version) series; urgency=...`
    and the newest entry comes first, so everything before the installed version's entry is
    what the pending update fixes."""
    cves: list[str] = []
    for line in changelog.splitlines():
        header = re.match(r"^\S+ \(([^)]+)\) ", line)
        if header:
            if header.group(1) == installed_version:
                break
            continue
        cves.extend(_CVE_RE.findall(line))
    return sorted(set(cves))


def _collect_packages(host: str) -> list[dict]:
    status, out = RUNNER(host, "dpkg-query -W -f='${Package}\\t${Version}\\n'; echo '@@UPGRADABLE@@'; "
                               "LC_ALL=C apt list --upgradable 2>/dev/null")
    dpkg_out, _, upg_out = out.partition("@@UPGRADABLE@@")
    packages = parse_dpkg(dpkg_out)
    if not packages:
        raise HTTPException(503, f"dpkg-query on {host} returned no packages (status {status})")
    by_name = {p["name"]: p for p in packages}
    security = {n: i for n, i in parse_security_upgradable(upg_out).items() if n in by_name}

    names = sorted(security)
    with_cves = names[:MAX_CHANGELOG_PACKAGES]
    changelogs: dict[str, str] = {}
    if with_cves:
        # One SSH session for all changelogs (each is a small HTTP fetch on the node).
        script = "; ".join(f"echo '@@CHANGELOG {n}@@'; apt-get changelog {n} 2>/dev/null | head -n 400"
                           for n in with_cves)
        try:
            _, chlog_out = RUNNER(host, script)
            changelogs = split_changelogs(chlog_out)
        except HTTPException:
            logger.warning("changelog fetch failed on %s; reporting updates without CVE ids", host)

    for name in names:
        cves = parse_changelog_cves(changelogs.get(name, ""), security[name]["installed"]) if name in changelogs else []
        by_name[name]["security_update"] = {"version": security[name]["version"], "cves": cves}
    return packages


def split_changelogs(output: str) -> dict[str, str]:
    """Splits the combined `@@CHANGELOG <name>@@` script output back into per-package text."""
    result: dict[str, str] = {}
    current = None
    for line in output.splitlines():
        m = re.match(r"^@@CHANGELOG (\S+)@@$", line)
        if m:
            current = m.group(1)
            result[current] = ""
        elif current is not None:
            result[current] += line + "\n"
    return result


@app.get("/packages")
def packages(host: str):
    _resolve(host)  # 404 for an unknown host
    return store_get(("packages", host))


# --------------------------------------------------------------------------
# Listening ports
# --------------------------------------------------------------------------
_LOOPBACK = ("127.", "[::1]", "::1")


def parse_ss(output: str) -> list[dict]:
    """`ss -H -tulnp` -> [{"port","protocol","process"}], loopback-only listeners dropped
    (they are not reachable from outside), duplicates (v4/v6) merged."""
    seen: dict[tuple[int, str], dict] = {}
    for line in output.splitlines():
        cols = line.split()
        if len(cols) < 5:
            continue
        proto, local = cols[0].lower(), cols[4]
        if proto not in ("tcp", "udp"):
            continue
        addr, _, port_s = local.rpartition(":")
        if not port_s.isdigit() or addr.startswith(_LOOPBACK):
            continue
        m = re.search(r'users:\(\("([^"]+)"', line)
        key = (int(port_s), proto)
        seen.setdefault(key, {"port": int(port_s), "protocol": proto, "process": m.group(1) if m else "unknown"})
    return sorted(seen.values(), key=lambda p: (p["port"], p["protocol"]))


def _collect_ports_and_sensor(host: str) -> tuple[list[dict], bool]:
    status, out = RUNNER(host, f"ss -H -tulnp; echo '@@SENSOR@@'; systemctl is-active {FALCO_UNIT}")
    ports_out, _, sensor_out = out.partition("@@SENSOR@@")
    ports = parse_ss(ports_out)
    if not ports:
        raise HTTPException(503, f"ss on {host} returned no listening sockets (status {status})")
    return ports, sensor_out.strip() == "active"


@app.get("/listening-ports")
def listening_ports(host: str):
    _resolve(host)
    return store_get(("ports", host))


# --------------------------------------------------------------------------
# Kernel-level alerts (Falco)
# --------------------------------------------------------------------------
_alerts: deque = deque(maxlen=5000)
_alerts_lock = threading.Lock()
_ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")


def _save_alerts() -> None:
    """Keeps alerts across collector restarts (a rebuild would otherwise wipe the evidence)."""
    with _alerts_lock:
        snapshot = list(_alerts)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _ALERTS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh)
        os.replace(tmp, _ALERTS_FILE)
    except OSError as exc:
        logger.warning("could not persist alerts to %s: %s", _ALERTS_FILE, exc)


def _load_alerts() -> None:
    try:
        with open(_ALERTS_FILE) as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return
    with _alerts_lock:
        for ts, alert in rows:
            _alerts.append((ts, alert))
    logger.info("restored %d alerts from %s", len(rows), _ALERTS_FILE)


def _normalise_alert(payload: dict) -> dict | None:
    priority = str(payload.get("priority", "")).lower()
    if PRIORITY_RANK.get(priority, 0) < PRIORITY_RANK.get(ALERT_MIN_PRIORITY, 2):
        return None
    host = payload.get("hostname") or (payload.get("output_fields") or {}).get("hostname") or payload.get("host")
    if not host or not payload.get("rule"):
        return None
    if ALERT_IGNORE_REGEX and re.search(ALERT_IGNORE_REGEX, f"{payload['rule']} {payload.get('output', '')}"):
        return None
    return {"host": host, "rule": payload["rule"], "priority": priority,
            "output": payload.get("output", ""), "time": payload.get("time") or datetime.now(timezone.utc).isoformat()}


@app.post("/falco")
async def falco_ingest(request: Request, token: str = ""):
    if not FALCO_TOKEN or not hmac.compare_digest(token, FALCO_TOKEN):
        raise HTTPException(401, "bad or missing token")
    body = await request.json()
    events = body if isinstance(body, list) else [body]
    stored = 0
    for event in events:
        alert = _normalise_alert(event) if isinstance(event, dict) else None
        if alert:
            with _alerts_lock:
                _alerts.append((time.time(), alert))
            stored += 1
    if stored:
        _save_alerts()
    return {"received": len(events), "stored": stored}


@app.get("/alerts")
def alerts(host: str | None = None):
    cutoff = time.time() - ALERT_TTL_SECONDS
    with _alerts_lock:
        current = [a for ts, a in _alerts if ts >= cutoff]
    if host is not None:
        # Absent sensor must read as "unknown", not "no alerts".
        _resolve(host)
        if not store_get(("sensor", host)):
            raise HTTPException(503, f"kernel sensor ({FALCO_UNIT}) is not active on {host}")
        current = [a for a in current if a["host"] == host]
    return sorted(current, key=lambda a: PRIORITY_RANK.get(a["priority"], 0), reverse=True)


# --------------------------------------------------------------------------
# Keystone token issuance (from the Apache access log)
# --------------------------------------------------------------------------
_ACCESS_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "POST /v3/auth/tokens[^"]*" (?P<status>\d{3})'
)


def parse_keystone_access_log(text: str, ttl_seconds: int = KEYSTONE_TOKEN_TTL,
                              ignore_ips: set[str] | None = None) -> list[dict]:
    """Successful token issuances from an Apache access log.

    The log does not contain the user name or the real expiry, so `username` is
    `client:<ip>` and `expires_at` is issued_at + the configured Keystone TTL."""
    ignore = ignore_ips if ignore_ips is not None else KEYSTONE_IGNORE_IPS
    events = []
    for line in text.splitlines():
        m = _ACCESS_RE.match(line)
        if not m or m.group("status") != "201" or m.group("ip") in ignore:
            continue
        try:
            issued = datetime.strptime(m.group("ts"), "%d/%b/%Y:%H:%M:%S %z").astimezone(timezone.utc)
        except ValueError:
            continue
        events.append({
            "username": f"client:{m.group('ip')}", "project": "unknown", "source_ip": m.group("ip"),
            "issued_at": issued.isoformat().replace("+00:00", "Z"),
            "expires_at": (issued + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
        })
    return events


@app.get("/_sandbox/keystone/token-log")
def keystone_token_log():
    def produce():
        cmd = (f"docker exec {KEYSTONE_CONTAINER} tail -n {KEYSTONE_LOG_LINES} {KEYSTONE_LOG_PATH}")
        status, out = RUNNER(KEYSTONE_HOST, cmd)
        if status != 0:
            raise HTTPException(503, f"cannot read {KEYSTONE_LOG_PATH} in container {KEYSTONE_CONTAINER} "
                                     f"on {KEYSTONE_HOST} (exit {status})")
        return parse_keystone_access_log(out)
    return cached(("keystone",), 30, produce)


@app.get("/health")
def health():
    return {"status": "ok", "hosts": sorted(load_inventory())}
