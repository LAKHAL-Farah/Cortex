"""Roadmap 4.5 -- the weekly digest email: capacity trend, security posture,
and what the agents caught, built from the week's *real* data.

Three layers, deliberately separate so each can be tested alone:

1. ``collect_digest_data`` -- aggregates the last 7 days (vs. the 7 before,
   for trends) out of Prometheus, the forecast service, the security-finding
   cache, ``anomaly_events``, ``agent_traces`` and the remediation audit
   log into one plain, JSON-serialisable dict. Every external source is
   best-effort: a Prometheus or forecast outage degrades that *section*
   (``available: False``), never the whole digest -- an operator should still
   get Monday's email on the day something is broken.
2. ``generate_summary`` -- the "AI summary" block. One NVIDIA NIM call
   (same ``llm_client`` the agents use) that is only ever handed the
   aggregated dict and told not to go beyond it. Any failure -- no key,
   timeout, malformed output -- falls back to a deterministic summary built
   from the same numbers, so the digest never depends on the LLM being up.
3. ``send_due_digest`` / ``send_digest_now`` -- delivery. The scheduler
   tick is restart-safe and double-send-safe (see ``send_due_digest``).

Rendering lives in ``weekly_digest_email.py``.

Privacy note: the digest is built from counts, CVE ids/package names and
hostnames only. It never includes source IPs, auth-log lines or
security-group rule contents -- an email is a much leakier channel than the
RBAC-gated dashboard, so it carries what a *viewer* could already see.
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import crud, models
from . import alert_email

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
# Scheduler tick; the due-check itself is cheap (one row read), so this only
# bounds how late after the scheduled hour a digest can go out.
DIGEST_CHECK_INTERVAL_SECONDS = int(os.getenv("DIGEST_CHECK_INTERVAL_SECONDS", "300"))
# If the API was down at the scheduled time, still send on startup -- but only
# within this many hours of the slot. A digest that lands three days late is
# worse than waiting for the next one.
DIGEST_CATCHUP_HOURS = int(os.getenv("DIGEST_CATCHUP_HOURS", "24"))
DIGEST_LLM_TIMEOUT_SECONDS = float(os.getenv("DIGEST_LLM_TIMEOUT_SECONDS", "60"))

# Same queries the forecast dataset builder uses, so the digest's "CPU 62%"
# means exactly what the forecast page's "CPU 62%" means.
CAPACITY_QUERIES = {
    "cpu": '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    "memory": "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "disk": '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
}
FORECAST_METRIC_KEY = {"cpu_percent": "cpu", "memory_percent": "memory", "disk_percent": "disk"}

_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0, "normal": -1}


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.utcnow()


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _role_str(role: Any) -> str:
    return str(getattr(role, "value", role) or "")


def window_bounds(now: datetime) -> tuple[datetime, datetime, datetime]:
    """(week_start, prev_start, now) -- the digest's reporting window and the
    equally long one before it, which is what every 'vs last week' figure
    compares against."""
    return now - timedelta(days=WINDOW_DAYS), now - timedelta(days=2 * WINDOW_DAYS), now


def window_label(start: datetime, end: datetime) -> str:
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
    return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"


def latest_slot(now: datetime, weekday: int, hour: int) -> datetime:
    """The most recent scheduled send time at or before `now` (UTC)."""
    slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    slot -= timedelta(days=(now.weekday() - weekday) % 7)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def next_slot(now: datetime, weekday: int, hour: int) -> datetime:
    slot = latest_slot(now, weekday, hour)
    return slot + timedelta(days=7) if slot <= now else slot


# --------------------------------------------------------------------------
# Capacity
# --------------------------------------------------------------------------

def _split_points(points: list, week_start_ts: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Prometheus [[ts, "value"], ...] -> (this_week, previous_week) of
    (ts, float). Drops NaN/garbage samples (a scrape gap renders as "NaN")."""
    this_week: list[tuple[float, float]] = []
    prev_week: list[tuple[float, float]] = []
    for point in points or []:
        try:
            ts, value = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        (this_week if ts >= week_start_ts else prev_week).append((ts, value))
    return this_week, prev_week


def _daily_averages(points: list[tuple[float, float]], week_start: datetime) -> list[float | None]:
    start_ts = week_start.replace(tzinfo=timezone.utc).timestamp()
    buckets: list[list[float]] = [[] for _ in range(WINDOW_DAYS)]
    for ts, value in points:
        idx = int((ts - start_ts) // 86400)
        if 0 <= idx < WINDOW_DAYS:
            buckets[idx].append(value)
    return [_round(_avg(b)) for b in buckets]


def build_capacity_section(
    nodes: list[dict],
    series: dict[str, dict[str, list]] | None,
    now: datetime,
    forecast_warnings: list[dict] | None,
    quota_alerts: list[dict] | None,
) -> dict:
    """`nodes`: [{hostname, role, instance}]. `series`: {metric: {instance:
    prometheus_points}} -- None means Prometheus was unreachable."""
    week_start, _, _ = window_bounds(now)
    week_start_ts = week_start.replace(tzinfo=timezone.utc).timestamp()

    section: dict[str, Any] = {
        "available": bool(series) and any(series.values()),
        "fleet": {},
        "nodes": [],
        "forecast_warnings": forecast_warnings or [],
        "quota_alerts": quota_alerts or [],
    }
    if not section["available"]:
        return section

    per_node: dict[str, dict[str, Any]] = {n["hostname"]: {"hostname": n["hostname"], "role": n["role"]} for n in nodes}
    instance_to_host = {n["instance"]: n["hostname"] for n in nodes}

    for metric, by_instance in series.items():
        fleet_cur: list[float] = []
        fleet_prev: list[float] = []
        fleet_daily: list[list[float]] = [[] for _ in range(WINDOW_DAYS)]
        peak, peak_host = None, None

        for instance, points in (by_instance or {}).items():
            host = instance_to_host.get(instance)
            if host is None:
                continue  # a scraped target that isn't a registered node
            cur, prev = _split_points(points, week_start_ts)
            if not cur:
                continue
            cur_vals = [v for _, v in cur]
            node_avg, node_peak = _avg(cur_vals), max(cur_vals)
            prev_avg = _avg([v for _, v in prev])
            per_node[host][f"{metric}_avg"] = _round(node_avg)
            per_node[host][f"{metric}_peak"] = _round(node_peak)
            per_node[host][f"{metric}_delta"] = _round(node_avg - prev_avg) if prev_avg is not None else None
            fleet_cur.append(node_avg)
            if prev_avg is not None:
                fleet_prev.append(prev_avg)
            for i, day_avg in enumerate(_daily_averages(cur, week_start)):
                if day_avg is not None:
                    fleet_daily[i].append(day_avg)
            if peak is None or node_peak > peak:
                peak, peak_host = node_peak, host

        avg_now, avg_prev = _avg(fleet_cur), _avg(fleet_prev)
        section["fleet"][metric] = {
            "avg": _round(avg_now),
            "prev_avg": _round(avg_prev),
            "delta": _round(avg_now - avg_prev) if avg_now is not None and avg_prev is not None else None,
            "peak": _round(peak),
            "peak_host": peak_host,
            "daily": [_round(_avg(d)) for d in fleet_daily],
        }

    rows = [r for r in per_node.values() if any(k.endswith("_avg") for k in r)]
    rows.sort(key=lambda r: max(r.get("cpu_avg") or 0, r.get("memory_avg") or 0, r.get("disk_avg") or 0), reverse=True)
    section["nodes"] = rows
    return section


def _fetch_capacity_series(nodes: list[dict], now: datetime) -> dict[str, dict[str, list]] | None:
    """Two weeks of hourly samples per metric, one range query each. None if
    Prometheus can't be reached at all (the section then says so, rather
    than reporting a week of zeroes)."""
    from .prometheus_client import query_range  # lazy: pulls in `requests`

    _, prev_start, end = window_bounds(now)
    start_ts = prev_start.replace(tzinfo=timezone.utc).timestamp()
    end_ts = end.replace(tzinfo=timezone.utc).timestamp()
    out: dict[str, dict[str, list]] = {}
    failures = 0
    for metric, promql in CAPACITY_QUERIES.items():
        try:
            result = query_range(promql, start_ts, end_ts, step="1h")
        except Exception:
            logger.warning("weekly digest: prometheus range query failed for %s", metric, exc_info=True)
            failures += 1
            continue
        out[metric] = {r.get("metric", {}).get("instance", ""): r.get("values", []) for r in result}
    return None if failures == len(CAPACITY_QUERIES) else out


def _fetch_forecast_warnings(db_nodes: list[models.Node]) -> list[dict]:
    try:
        from .forecast_service import list_threshold_warnings  # lazy: pandas/joblib

        warnings = list_threshold_warnings([(n.hostname, n.ip_address) for n in db_nodes])
    except Exception:
        logger.warning("weekly digest: forecast warnings unavailable", exc_info=True)
        return []
    slim = []
    for w in warnings[:6]:
        slim.append({
            "hostname": w.get("hostname"),
            "metric": FORECAST_METRIC_KEY.get(w.get("metric"), w.get("metric")),
            "current": w.get("current_value"),
            "threshold": w.get("threshold"),
            "eta_days": w.get("eta_days"),
            "already_breached": bool(w.get("already_breached")),
        })
    return slim


def _quota_alerts(db: Session) -> list[dict]:
    rows = db.execute(
        select(models.QuotaAlert).where(models.QuotaAlert.severity != "normal").order_by(models.QuotaAlert.ratio.desc())
    ).scalars().all()
    return [
        {
            "project": r.project_name,
            "breach_type": r.breach_type,
            "resource": r.resource,
            "ratio": _round(r.ratio, 3),
            "severity": r.severity,
        }
        for r in rows[:5]
    ]


# --------------------------------------------------------------------------
# Security posture
# --------------------------------------------------------------------------

def build_security_section(cache_rows: list[Any], scan_runs: list[Any]) -> dict:
    """From `security_finding_cache` rows (current posture) and the week's
    `security_scan_runs` (how reliable the scanning itself was)."""
    totals = {"risky_rules": 0, "drift_groups": 0, "cves": 0, "port_mismatches": 0, "ebpf_alerts": 0, "auth_signals": 0}
    cve_by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    cves: list[dict] = []
    hosts_with_signal: list[str] = []
    degraded_hosts = 0

    for row in cache_rows:
        raw = row.raw_data or {}
        sg, cve = raw.get("sec_group_signal") or {}, raw.get("cve_signal") or {}
        exposed, ebpf, auth = raw.get("exposed_port_signal") or {}, raw.get("ebpf_signal") or {}, raw.get("auth_signal") or {}
        totals["risky_rules"] += len(sg.get("risky_rules") or [])
        totals["drift_groups"] += len(sg.get("drift") or [])
        totals["port_mismatches"] += len(exposed.get("mismatches") or [])
        totals["ebpf_alerts"] += len(ebpf.get("alerts") or [])
        totals["auth_signals"] += 1 if auth.get("has_signal") else 0
        for match in cve.get("matches") or []:
            sev = str(match.get("severity", "low")).lower()
            totals["cves"] += 1
            cve_by_severity[sev] = cve_by_severity.get(sev, 0) + 1
            cves.append({
                "cve_id": match.get("cve_id"),
                "severity": sev,
                "package": match.get("package"),
                "installed_version": match.get("installed_version"),
                "fixed_version": match.get("fixed_version"),
                "hostname": row.hostname,
            })
        if row.has_signal:
            hosts_with_signal.append(row.hostname)
        if row.degraded:
            degraded_hosts += 1

    cves.sort(key=lambda c: _SEVERITY_RANK.get(c["severity"], 0), reverse=True)

    runs_total = len(scan_runs)
    runs_ok = sum(1 for r in scan_runs if r.status == "ok")
    return {
        "available": bool(cache_rows),
        "nodes_scanned": len(cache_rows),
        "nodes_with_signal": len(hosts_with_signal),
        "hosts_with_signal": hosts_with_signal[:8],
        "degraded_hosts": degraded_hosts,
        "totals": totals,
        "total_signals": sum(totals.values()),
        "cve_by_severity": cve_by_severity,
        "top_cves": cves[:5],
        "scan_runs": runs_total,
        "scan_ok_pct": _round(100.0 * runs_ok / runs_total) if runs_total else None,
    }


# --------------------------------------------------------------------------
# What the agents caught
# --------------------------------------------------------------------------

def build_anomaly_stats(events: list[Any], prev_count: int, open_now: int) -> dict:
    by_sev = {"critical": 0, "high": 0, "medium": 0}
    host_counts: dict[str, int] = {}
    ttr_minutes: list[float] = []
    resolved = manual = 0
    for e in events:
        by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
        host_counts[e.hostname] = host_counts.get(e.hostname, 0) + 1
        if e.resolved_at is not None:
            resolved += 1
            manual += 1 if e.resolution_type == "manual" else 0
            if e.started_at is not None:
                ttr_minutes.append((e.resolved_at - e.started_at).total_seconds() / 60.0)

    notable = sorted(
        events,
        key=lambda e: (_SEVERITY_RANK.get(e.severity, 0), abs(e.z_score or 0.0)),
        reverse=True,
    )[:5]
    return {
        "total": len(events),
        "prev_total": prev_count,
        "by_severity": by_sev,
        "resolved": resolved,
        "auto_resolved": resolved - manual,
        "manually_resolved": manual,
        "still_open": open_now,
        "avg_resolution_minutes": _round(_avg(ttr_minutes), 0),
        "top_hosts": sorted(host_counts.items(), key=lambda kv: kv[1], reverse=True)[:3],
        "notable": [
            {
                "hostname": e.hostname,
                "metric": e.metric_name,
                "severity": e.severity,
                "value": _round(e.current_value, 2),
                "z_score": _round(e.z_score, 1),
                "started_at": e.started_at.isoformat() if e.started_at else None,
                "resolved": e.resolved_at is not None,
            }
            for e in notable
        ],
    }


def _remediation_stats(db: Session, since: datetime) -> dict:
    since_aware = since.replace(tzinfo=timezone.utc)
    events = db.execute(
        select(models.RemediationAuditEntry).where(models.RemediationAuditEntry.created_at >= since_aware)
        .order_by(models.RemediationAuditEntry.created_at.desc())
    ).scalars().all()
    counts = {k: 0 for k in ("proposed", "approved", "rejected", "executed", "execution_failed")}
    for ev in events:
        if ev.event in counts:
            counts[ev.event] += 1

    recent = []
    for ev in events:
        if ev.event not in ("executed", "approved", "execution_failed") or len(recent) >= 4:
            continue
        proposal = db.get(models.RemediationProposal, ev.proposal_row_id)
        body = (proposal.proposal if proposal else {}) or {}
        recent.append({
            "event": ev.event,
            "title": body.get("symptom_title") or ev.proposal_id,
            "host": body.get("host"),
            "actor": ev.actor_username,
            "at": ev.created_at.isoformat() if ev.created_at else None,
        })
    return {**counts, "recent": recent}


def _agent_activity(db: Session, since: datetime, prev_since: datetime) -> dict:
    stats = crud.agent_trace_stats(db, since=since)
    prev_total = db.scalar(
        select(func.count()).select_from(models.AgentTrace).where(
            models.AgentTrace.created_at >= prev_since, models.AgentTrace.created_at < since
        )
    ) or 0
    # agent_trace_stats is `>= since` only; the 'since' bound is the week
    # start here, so the "now" upper bound is implicit.
    return {
        "questions": stats["total_invocations"],
        "prev_questions": prev_total,
        "by_agent": sorted(
            (
                {"agent": r["target_agent"] or "router", "count": r["count"]}
                for r in stats["by_agent"]
            ),
            key=lambda r: r["count"], reverse=True,
        )[:4],
        "critic_flagged_pct": _round(100.0 * stats["critic_flagged_rate"], 1),
        "degraded_pct": _round(100.0 * stats["degraded_rate"], 1),
    }


# --------------------------------------------------------------------------
# Overall posture + assembly
# --------------------------------------------------------------------------

def compute_posture(data: dict) -> dict:
    reasons: list[str] = []
    level = "ok"

    def bump(to: str, reason: str) -> None:
        nonlocal level
        reasons.append(reason)
        if {"ok": 0, "attention": 1, "action": 2}[to] > {"ok": 0, "attention": 1, "action": 2}[level]:
            level = to

    anomalies, sec, cap = data["agents"]["anomalies"], data["security"], data["capacity"]
    if anomalies["still_open"]:
        crit = anomalies["by_severity"].get("critical", 0)
        bump("action" if crit else "attention", f"{anomalies['still_open']} alert(s) still open")
    if sec["cve_by_severity"].get("critical", 0):
        bump("action", f"{sec['cve_by_severity']['critical']} critical CVE(s) unpatched")
    if sec["total_signals"] and not sec["cve_by_severity"].get("critical", 0):
        bump("attention", f"{sec['total_signals']} open security signal(s)")
    if any(w.get("already_breached") for w in cap["forecast_warnings"]):
        bump("action", "a resource is already past its threshold")
    elif cap["forecast_warnings"]:
        bump("attention", f"{len(cap['forecast_warnings'])} resource(s) projected to breach a threshold")
    if any(q["severity"] == "critical" for q in cap["quota_alerts"]):
        bump("action", "a project quota/budget is critical")
    elif cap["quota_alerts"]:
        bump("attention", "a project is approaching a quota/budget cap")

    label = {"ok": "All clear", "attention": "Needs attention", "action": "Action required"}[level]
    return {"level": level, "label": label, "reasons": reasons}


def collect_digest_data(db: Session, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    week_start, prev_start, _ = window_bounds(now)

    db_nodes = crud.list_nodes(db)
    nodes = [
        {"hostname": n.hostname, "role": _role_str(n.role), "instance": f"{n.ip_address}:{n.exporter_port}"}
        for n in db_nodes
    ]

    capacity = build_capacity_section(
        nodes,
        _fetch_capacity_series(nodes, now),
        now,
        _fetch_forecast_warnings(db_nodes),
        _quota_alerts(db),
    )

    cache_rows = crud.list_security_finding_cache(db)
    scan_runs = db.execute(
        select(models.SecurityScanRun).where(models.SecurityScanRun.started_at >= week_start)
    ).scalars().all()
    security = build_security_section(cache_rows, scan_runs)

    events = db.execute(
        select(models.AnomalyEvent).where(models.AnomalyEvent.started_at >= week_start)
    ).scalars().all()
    prev_count = db.scalar(
        select(func.count()).select_from(models.AnomalyEvent).where(
            models.AnomalyEvent.started_at >= prev_start, models.AnomalyEvent.started_at < week_start
        )
    ) or 0
    open_now = db.scalar(
        select(func.count()).select_from(models.AnomalyEvent).where(models.AnomalyEvent.resolved_at.is_(None))
    ) or 0

    data = {
        "generated_at": now.isoformat() + "Z",
        "window": {"start": week_start.isoformat() + "Z", "end": now.isoformat() + "Z", "label": window_label(week_start, now)},
        "node_count": len(nodes),
        "capacity": capacity,
        "security": security,
        "agents": {
            "anomalies": build_anomaly_stats(events, prev_count, open_now),
            "remediation": _remediation_stats(db, week_start),
            "activity": _agent_activity(db, week_start, prev_start),
        },
    }
    data["posture"] = compute_posture(data)
    return data


# --------------------------------------------------------------------------
# AI summary (NVIDIA NIM) with deterministic fallback
# --------------------------------------------------------------------------

_SUMMARY_SYSTEM_PROMPT = (
    "You write the executive summary at the top of a weekly infrastructure digest email for an "
    "OpenStack operations team. You are given a JSON object of this week's real measurements. "
    "Rules: use ONLY facts present in the JSON -- never invent hosts, numbers, causes or events. "
    "If a section has available=false, say its data was unavailable rather than guessing. "
    "Be concrete (name hosts and figures), calm and professional, no hype, no emojis, no markdown.\n"
    "Reply with ONLY a JSON object, no prose around it, in exactly this shape:\n"
    '{"headline": "<one sentence, max 28 words, the single most important takeaway>", '
    '"highlights": ["<2 to 4 short sentences covering capacity, security, and what the agents caught>"], '
    '"actions": ["<0 to 3 concrete recommended next steps, each starting with a verb; empty list if all clear>"]}'
)


def _compact_for_llm(data: dict) -> dict:
    """Trim to what the model needs -- smaller prompt, fewer ways to wander."""
    cap = data["capacity"]
    return {
        "window": data["window"]["label"],
        "nodes_monitored": data["node_count"],
        "overall_posture": data["posture"],
        "capacity": {
            "available": cap["available"],
            "fleet_avg_percent_with_change_vs_last_week_in_points": {
                m: {k: v for k, v in f.items() if k in ("avg", "delta", "peak", "peak_host")} for m, f in cap["fleet"].items()
            },
            "busiest_nodes": [
                {k: v for k, v in n.items() if k in ("hostname", "role", "cpu_avg", "memory_avg", "disk_avg")} for n in cap["nodes"][:3]
            ],
            "projected_threshold_breaches": cap["forecast_warnings"],
            "quota_or_budget_alerts": cap["quota_alerts"],
        },
        "security": {k: v for k, v in data["security"].items() if k in (
            "available", "nodes_scanned", "nodes_with_signal", "totals", "cve_by_severity", "top_cves", "scan_ok_pct")},
        "agents": {
            "alerts_detected": {k: v for k, v in data["agents"]["anomalies"].items() if k != "top_hosts"},
            "remediation": data["agents"]["remediation"],
            "copilot_questions": data["agents"]["activity"]["questions"],
        },
    }


def fallback_summary(data: dict) -> dict:
    """Deterministic summary from the same numbers -- what ships when the LLM
    is unreachable, so the block is never empty and never wrong."""
    cap, sec = data["capacity"], data["security"]
    ag, rem = data["agents"]["anomalies"], data["agents"]["remediation"]
    posture = data["posture"]

    headline = {
        "ok": "A quiet week: no open alerts, no projected capacity breaches and no open security signals.",
        "attention": "Mostly stable week, with a few items worth reviewing: " + "; ".join(posture["reasons"][:2]) + ".",
        "action": "Action needed: " + "; ".join(posture["reasons"][:2]) + ".",
    }[posture["level"]]

    highlights: list[str] = []
    cpu, mem = cap["fleet"].get("cpu"), cap["fleet"].get("memory")
    if cap["available"] and cpu and cpu.get("avg") is not None:
        delta = cpu.get("delta")
        trend = "" if delta is None else f" ({delta:+.1f} pts vs last week)"
        mem_part = f" and memory {mem['avg']:.0f}%" if mem and mem.get("avg") is not None else ""
        highlights.append(f"Fleet CPU averaged {cpu['avg']:.0f}%{mem_part}{trend}.")
    elif not cap["available"]:
        highlights.append("Capacity metrics were unavailable this week.")
    if sec["available"]:
        highlights.append(
            f"{sec['total_signals']} open security signal(s) across {sec['nodes_scanned']} scanned node(s)."
            if sec["total_signals"] else f"No open security signals across {sec['nodes_scanned']} scanned node(s)."
        )
    highlights.append(
        f"Cortex detected {ag['total']} alert(s) this week and {ag['resolved']} resolved; "
        f"{rem['proposed']} fix(es) proposed, {rem['executed']} executed."
    )

    actions: list[str] = []
    for w in cap["forecast_warnings"][:2]:
        when = "is already past" if w["already_breached"] else f"is projected to reach {w['threshold']:.0f}% in about {w['eta_days']} day(s) on"
        actions.append(f"Plan capacity: {w['metric']} {when} {w['hostname']}.")
    if sec["cve_by_severity"].get("critical"):
        actions.append("Patch the critical CVE(s) listed in the security section.")
    if ag["still_open"]:
        actions.append(f"Review the {ag['still_open']} alert(s) that are still open.")
    return {"headline": headline, "highlights": highlights[:4], "actions": actions[:3], "source": "fallback"}


def _parse_summary(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    headline = obj.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        return None

    def clean(items: Any, limit: int) -> list[str]:
        if not isinstance(items, list):
            return []
        return [s.strip()[:320] for s in items if isinstance(s, str) and s.strip()][:limit]

    highlights = clean(obj.get("highlights"), 4)
    if not highlights:
        return None
    return {"headline": headline.strip()[:260], "highlights": highlights, "actions": clean(obj.get("actions"), 3), "source": "ai"}


def _call_llm(payload: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage  # lazy
    from .llm_client import get_chat_model

    # Generous budget: the default model is a *reasoning* model whose thinking
    # tokens count against max_tokens, so a tight cap truncates the JSON answer.
    llm = get_chat_model(temperature=0.2, max_tokens=3000, tier="reasoning")
    response = llm.invoke([SystemMessage(content=_SUMMARY_SYSTEM_PROMPT), HumanMessage(content=payload)])
    content = response.content
    if isinstance(content, list):  # some providers return content blocks
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return content or ""


def generate_summary(data: dict) -> dict:
    fallback = fallback_summary(data)
    if not os.environ.get("NVIDIA_API_KEY"):
        return fallback
    payload = json.dumps(_compact_for_llm(data), default=str)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        text = pool.submit(_call_llm, payload).result(timeout=DIGEST_LLM_TIMEOUT_SECONDS)
        parsed = _parse_summary(text)
        if parsed is None:
            logger.warning(
                "weekly digest: LLM returned unusable output, using fallback summary. Raw output (first 400 chars): %r",
                (text or "")[:400],
            )
            return fallback
        return parsed
    except FutureTimeout:
        logger.warning("weekly digest: LLM timed out after %ss, using fallback summary", DIGEST_LLM_TIMEOUT_SECONDS)
        return fallback
    except Exception:
        logger.warning("weekly digest: LLM call failed, using fallback summary", exc_info=True)
        return fallback
    finally:
        pool.shutdown(wait=False)


# --------------------------------------------------------------------------
# Delivery + scheduling
# --------------------------------------------------------------------------

def build_digest_email(db: Session, now: datetime | None = None, use_ai: bool = True) -> tuple[str, str, str, dict]:
    """(subject, text_body, html_body, data) -- shared by send and preview.
    `use_ai=False` skips the LLM call (preview without spending tokens)."""
    from .weekly_digest_email import render_html, render_text, subject_line  # lazy: avoid import cycle

    data = collect_digest_data(db, now)
    summary = generate_summary(data) if use_ai else fallback_summary(data)
    return subject_line(data), render_text(data, summary), render_html(data, summary), data


def send_digest_now(db: Session) -> dict:
    """Manual 'Send now' -- sends to the configured recipient regardless of
    the schedule and does NOT touch `digest_last_sent_at`, so testing the
    email never suppresses (or triggers) the scheduled one."""
    settings = alert_email.get_settings(db)
    subject, text, html, data = build_digest_email(db)
    alert_email.send_email(settings.recipient_email, subject, text, html)
    return {"recipient": settings.recipient_email, "subject": subject, "posture": data["posture"]["level"]}


def is_due(settings: models.AlertEmailSettings, now: datetime) -> bool:
    slot = latest_slot(now, settings.digest_weekday, settings.digest_hour_utc)
    if now - slot > timedelta(hours=DIGEST_CATCHUP_HOURS):
        return False
    return settings.digest_last_sent_at is None or settings.digest_last_sent_at < slot


def send_due_digest(db: Session, now: datetime | None = None) -> str:
    """Scheduler tick. Returns what it did ('disabled' | 'smtp_not_configured'
    | 'not_due' | 'seeded' | 'sent' | 'failed') so it is testable and loggable.

    Safety properties:
    - Restart-safe: "have we sent this week's digest" lives in the database
      (`digest_last_sent_at`), not in process memory.
    - Double-send-safe: the row is locked (SELECT .. FOR UPDATE) and
      `digest_last_sent_at` is advanced *before* sending, so a second API
      replica ticking at the same moment sees it as already sent. If the
      send then fails, the previous value is restored so the next tick retries.
    - No surprise on first deploy: with no history the schedule is seeded to
      the most recent slot instead of firing a digest immediately.
    """
    now = now or _utcnow()
    settings = db.execute(
        select(models.AlertEmailSettings).where(models.AlertEmailSettings.id == 1).with_for_update()
    ).scalar_one_or_none()
    if settings is None:
        settings = alert_email.get_settings(db)
    if not settings.digest_enabled:
        db.rollback()
        return "disabled"
    if not alert_email.smtp_configured():
        db.rollback()
        return "smtp_not_configured"

    if settings.digest_last_sent_at is None:
        settings.digest_last_sent_at = latest_slot(now, settings.digest_weekday, settings.digest_hour_utc)
        db.commit()
        return "seeded"
    if not is_due(settings, now):
        db.rollback()
        return "not_due"

    previous = settings.digest_last_sent_at
    recipient = settings.recipient_email
    settings.digest_last_sent_at = now
    db.commit()  # claim the slot (releases the row lock)
    try:
        subject, text, html, _ = build_digest_email(db, now)
        alert_email.send_email(recipient, subject, text, html)
    except Exception:
        logger.exception("weekly digest delivery failed; will retry on the next tick")
        db.rollback()
        settings = alert_email.get_settings(db)
        settings.digest_last_sent_at = previous
        db.commit()
        return "failed"
    logger.info("weekly digest sent to %s", recipient)
    return "sent"
