"""Keystone token-abuse detection (Phase Sec-5c).

Layer: **Identity** -- neither Node nor Instance. Tokens belong to
operators/services authenticating against Keystone, not to a host or a
VM: this check has no `hostname` and no `instance_id` in its output, and
is deliberately never folded into agents/nodes/security.py's four (now
five, with Sec-5a) per-node sub-checks the way exposed_ports.py's node
check was -- Keystone token abuse is a fleet/project-wide question, and
forcing it into a per-host table shape would misrepresent what it
actually is (see the roadmap doc's own §5 "Phase Sec-5c" section, and
`routers/security.py`'s `/keystone-tokens` endpoint below, which reports
one fleet-wide finding rather than one row per node).

Same two-piece split as cve_feed.py/exposed_ports.py, for the same
reason (a pure rule-matcher that's trivially unit-testable without
mocking HTTP):

1. `get_token_issuance_log()` -- an HTTP client against a token-issuance
   history source. This is genuinely a harder collector problem than
   Sec-3/Sec-5a's: **a real Keystone deployment has no built-in, directly
   queryable "list every token ever issued" API** -- tokens are opaque
   (Fernet) by design, and a real audit trail normally comes from either
   Keystone's own CADF audit-notification middleware piped to a log
   store, or a purpose-built sidecar that taps `POST /v3/auth/tokens`
   itself. This client is written against exactly that second shape: a
   small HTTP endpoint returning already-structured issuance events,
   same as cve_feed.py/exposed_ports.py's collectors. As of Phase Sec-5c,
   the sandbox (infra/openstack-sim/app.py) serves this from `POST
   /v3/auth/tokens`'s own in-memory issuance log via `GET /_sandbox/
   keystone/token-log` -- a genuine, if simulated, issuance history to
   query, extended specifically for this phase (see that file's own
   comment). Outside the sandbox, point `CORTEX_KEYSTONE_TOKEN_LOG_URL`
   at a real CADF-notification consumer or audit-middleware sidecar and
   this starts reading real issuance events there too; with nothing
   deployed yet, every call fails with a connection error, degraded by
   the caller the same way every other external read in this codebase
   is.

   Expected response shape from `.../_sandbox/keystone/token-log` (or a
   real equivalent):
       [{"username": str, "project": str, "source_ip": str,
         "issued_at": ISO-8601 str, "expires_at": ISO-8601 str}, ...]

2. `find_abusive_token_patterns(events, now=None)` -- a pure function, no
   network calls. Flags three small, explicit, documented patterns --
   deliberately a hand-written rule-set, not a vague ML model on day one,
   the exact same tradeoff `security_audit._risk_reason` makes for
   "overly permissive":

   - **Rapid re-issuance**: the same user issued `_RAPID_REISSUE_COUNT`
     or more tokens within `_RAPID_REISSUE_WINDOW_SECONDS` -- a real
     operator re-authenticating that often in that short a window is
     unusual; a compromised credential being used to mint a fresh token
     per request (to dodge a revocation, or because a scripted attacker
     never bothers to cache one) looks exactly like this.
   - **Unexpected source IP**: a token issued from an IP outside
     `_EXPECTED_CIDRS` -- deliberately a small, explicit, operator-
     configurable allowlist (`CORTEX_KEYSTONE_EXPECTED_CIDRS`, comma-
     separated CIDRs) rather than a learned baseline, so a fresh
     deployment has an honest "not configured, can't check this" state
     instead of a silent false negative.
   - **Long-lived token**: a token whose `expires_at - issued_at` exceeds
     `_MAX_SANE_TTL_SECONDS` -- a token alive far longer than a normal
     session TTL is a common sign of a deliberately-widened credential
     (or a misconfigured issuer), independent of who requested it or
     from where.
"""
import ipaddress
import logging
import os
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# Same "sandbox points this at openstack-sim, production points it at a
# real collector" shape every other client in this package uses (see
# cve_feed.PACKAGE_INVENTORY_URL / exposed_ports.LISTENING_PORTS_URL).
TOKEN_LOG_URL = os.environ.get("CORTEX_KEYSTONE_TOKEN_LOG_URL", "http://keystone-audit:9113")

# Rapid re-issuance: same user, this many (or more) tokens inside this
# many seconds. 3-in-10s is deliberately tight -- a human re-logging-in
# that often is already unusual; anything scripted re-issuing per-request
# will blow far past this.
_RAPID_REISSUE_COUNT = 3
_RAPID_REISSUE_WINDOW_SECONDS = 10

# A token alive longer than this is flagged regardless of who holds it or
# where it was issued from. 12h is deliberately generous (a real
# Keystone deployment's own default token TTL is usually 1h) -- this
# exists to catch a token clearly outside any sane session length, not to
# second-guess a legitimate, slightly-long-lived one.
_MAX_SANE_TTL_SECONDS = 12 * 60 * 60

# Comma-separated CIDRs an operator expects tokens to be issued from
# (e.g. "10.0.0.0/8,192.168.1.0/24"). Empty by default -- an unconfigured
# deployment reports "not configured" for this one pattern rather than
# silently treating every IP as expected (see module docstring). Read
# live in `_expected_networks()` below, not cached at import time, so
# this is actually reconfigurable without a process restart.


def _expected_networks() -> list:
    raw = os.environ.get("CORTEX_KEYSTONE_EXPECTED_CIDRS", "")
    networks = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            networks.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            logger.warning("keystone_audit: ignoring invalid CIDR in CORTEX_KEYSTONE_EXPECTED_CIDRS: %r", chunk)
    return networks


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Keystone/openstack-sim both emit trailing "Z" -- datetime.
        # fromisoformat only accepts "+00:00" before Python 3.11, so
        # normalize rather than assume a modern interpreter.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_token_issuance_log() -> list[dict]:
    """Every token-issuance event this collector has recorded, oldest or
    newest first (order not guaranteed -- `find_abusive_token_patterns`
    sorts what it needs). Raises on a connection/timeout/non-2xx -- see
    module docstring on why that's the expected, gracefully-degraded-by-
    the-caller state without a real collector deployed yet."""
    response = requests.get(f"{TOKEN_LOG_URL}/_sandbox/keystone/token-log", timeout=8)
    response.raise_for_status()
    return response.json()


def find_abusive_token_patterns(events: list[dict], now: datetime | None = None) -> dict:
    """Pure -- no network calls. Applies the three small, explicit rules
    from the module docstring to `events` (the shape `get_token_issuance_
    log` returns) and returns every hit, grouped by pattern.
    """
    now = now or datetime.now(timezone.utc)
    expected_networks = _expected_networks()

    rapid_reissue: list[dict] = []
    unexpected_ip: list[dict] = []
    long_lived: list[dict] = []

    by_user: dict[str, list[dict]] = {}
    for event in events:
        username = event.get("username") or "unknown"
        by_user.setdefault(username, []).append(event)

    for username, user_events in by_user.items():
        parsed = sorted(
            (e for e in user_events if _parse_iso(e.get("issued_at")) is not None),
            key=lambda e: _parse_iso(e["issued_at"]),
        )
        for i in range(len(parsed)):
            window_start = _parse_iso(parsed[i]["issued_at"])
            count_in_window = 1
            for j in range(i + 1, len(parsed)):
                delta = (_parse_iso(parsed[j]["issued_at"]) - window_start).total_seconds()
                if delta <= _RAPID_REISSUE_WINDOW_SECONDS:
                    count_in_window += 1
                else:
                    break
            if count_in_window >= _RAPID_REISSUE_COUNT:
                rapid_reissue.append({
                    "username": username,
                    "count": count_in_window,
                    "window_seconds": _RAPID_REISSUE_WINDOW_SECONDS,
                    "first_issued_at": parsed[i]["issued_at"],
                })
                break  # one flag per user is enough to raise the pattern

    if expected_networks:
        for event in events:
            source_ip = event.get("source_ip")
            if not source_ip:
                continue
            try:
                addr = ipaddress.ip_address(source_ip)
            except ValueError:
                continue
            if not any(addr in net for net in expected_networks):
                unexpected_ip.append({
                    "username": event.get("username"),
                    "source_ip": source_ip,
                    "issued_at": event.get("issued_at"),
                })

    for event in events:
        issued_at = _parse_iso(event.get("issued_at"))
        expires_at = _parse_iso(event.get("expires_at"))
        if issued_at is None or expires_at is None:
            continue
        ttl_seconds = (expires_at - issued_at).total_seconds()
        if ttl_seconds > _MAX_SANE_TTL_SECONDS:
            long_lived.append({
                "username": event.get("username"),
                "ttl_seconds": ttl_seconds,
                "issued_at": event.get("issued_at"),
                "expires_at": event.get("expires_at"),
            })

    return {
        "rapid_reissue": rapid_reissue,
        "unexpected_ip": unexpected_ip,
        "unexpected_ip_checked": bool(expected_networks),
        "long_lived": long_lived,
        "event_count": len(events),
    }
