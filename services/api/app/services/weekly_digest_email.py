"""Rendering for the weekly digest (roadmap 4.5): data dict + summary dict in,
email-safe HTML and a plain-text alternative out. No I/O, no database.

Visual language is the web app's own (services/web/app/globals.css): the
same canvas/panel/border greys, the single coral accent, the same
ok/warn/crit status colors, Sora for headings, Inter for body, IBM Plex Mono
for numbers and eyebrows -- so the email reads as the product, not a
generic template.

Email-client reality drives the construction: nested `<table role=presentation>`
layout, inline styles for everything that matters, bars and charts drawn with
table cells / sized divs (no images, so nothing is blocked and nothing needs
hosting), web fonts as a progressive enhancement over solid system fallbacks,
and a small `<style>` block only for mobile stacking and dark mode. Every
dynamic string goes through `html.escape`.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from html import escape

# -- palette (mirrors globals.css :root / .dark) ---------------------------
BG, CARD, BORDER, SOFT = "#F5F6F8", "#FFFFFF", "#E3E6EA", "#ECEEF1"
TEXT, DIM, FAINT, MUTED = "#16181D", "#4A4F57", "#6B7280", "#9AA1A9"
ACCENT, ACCENT_SOFT = "#E15B3C", "#FCEEE9"
OK, OK_SOFT = "#2F9E68", "#E9F5EF"
WARN, WARN_SOFT = "#C98A1D", "#FAF3E3"
CRIT, CRIT_SOFT = "#D64545", "#FBEAEA"
BLUE, PURPLE = "#3B7EC4", "#7C6FE0"
INK, INK2 = "#0D0F13", "#15171C"

F_BODY = "Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
F_DISPLAY = "Sora,Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
F_MONO = "'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,'Courier New',monospace"

_POSTURE = {
    "ok": (OK, OK_SOFT, "p-ok"),
    "attention": (WARN, WARN_SOFT, "p-warn"),
    "action": (CRIT, CRIT_SOFT, "p-crit"),
}
_SEV = {
    "critical": (CRIT, CRIT_SOFT, "p-crit"),
    "high": (WARN, WARN_SOFT, "p-warn"),
    "medium": (PURPLE, "#EFEDFB", "p-med"),
    "low": (FAINT, SOFT, "p-neutral"),
}

_STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sora:wght@600;700&family=IBM+Plex+Mono:wght@500;600&display=swap');
body{margin:0;padding:0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
table{border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0}
a{text-decoration:none}
@media only screen and (max-width:560px){
  .shell{width:100%!important}
  .px{padding-left:16px!important;padding-right:16px!important}
  .kpi{display:inline-block!important;width:50%!important;box-sizing:border-box}
  .stack{display:block!important;width:100%!important;box-sizing:border-box}
  .hide-sm{display:none!important}
  .h1{font-size:24px!important}
}
@media (prefers-color-scheme:dark){
  .em-bg{background:#0D0F13!important}
  .em-card{background:#15171C!important;border-color:#262930!important}
  .em-soft{background:#1E2026!important}
  .em-bd{border-color:#262930!important}
  .em-text{color:#EDEEF0!important}
  .em-dim{color:#C2C6CC!important}
  .em-faint{color:#8B909A!important}
  .em-track{background:#262930!important}
  .p-ok{background:rgba(62,185,129,.16)!important;color:#3EB981!important}
  .p-warn{background:rgba(217,165,54,.16)!important;color:#D9A536!important}
  .p-crit{background:rgba(229,104,107,.16)!important;color:#E5686B!important}
  .p-med{background:rgba(155,144,238,.18)!important;color:#9B90EE!important}
  .p-neutral{background:rgba(138,147,166,.16)!important;color:#8B909A!important}
  .ai-card{background:#1B1714!important}
}
"""


# --------------------------------------------------------------------------
# tiny helpers
# --------------------------------------------------------------------------

def _e(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def _pct(value, digits: int = 0) -> str:
    return "–" if value is None else f"{value:.{digits}f}%"


def _human_metric(name: str) -> str:
    known = {"cpu": "CPU", "memory": "Memory", "disk": "Disk", "cpu_usage": "CPU usage", "ram_usage": "Memory usage"}
    if name in known:
        return known[name]
    acronyms = {"cpu": "CPU", "ram": "RAM", "ssh": "SSH", "ip": "IP", "io": "I/O", "api": "API", "cve": "CVE"}
    words = [acronyms.get(w.lower(), w) for w in str(name or "").replace("_", " ").split()]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _level_color(value) -> str:
    if value is None:
        return MUTED
    return CRIT if value >= 85 else WARN if value >= 70 else OK


def _delta(value, unit: str = " pts", up_is_bad: bool = True, steady_below: float = 0.5,
           quiet_below: float = 0.0, neutral: bool = False) -> str:
    """'▲ 3.1 pts' coloured by whether the direction is good or bad. Moves
    smaller than `quiet_below` are shown in muted grey (a +2 pt wobble on a
    healthy fleet isn't news); `neutral=True` never colours at all (e.g.
    Copilot usage, where more isn't better or worse)."""
    if value is None:
        return f'<span class="em-faint" style="color:{FAINT}">no prior week</span>'
    if abs(value) < steady_below:
        return f'<span class="em-faint" style="color:{FAINT}">● steady</span>'
    up = value > 0
    bad = up == up_is_bad
    color = CRIT if bad else OK
    if neutral or abs(value) < quiet_below:
        color = DIM
    mag = f"{abs(value):.0f}" if unit == "" else f"{abs(value):.1f}"
    cls = ' class="em-dim"' if color == DIM else ""
    return f'<span{cls} style="color:{color};font-weight:600">{"▲" if up else "▼"} {mag}{_e(unit)}</span>'


def _pill(text: str, kind: tuple, mono: bool = False) -> str:
    color, soft, cls = kind
    font = F_MONO if mono else F_BODY
    return (
        f'<span class="{cls}" style="display:inline-block;padding:3px 9px;border-radius:999px;background:{soft};color:{color};'
        f'font:600 11px/1.3 {font};letter-spacing:.02em;white-space:nowrap">{_e(text)}</span>'
    )


def _sev_pill(sev: str) -> str:
    return _pill(str(sev).upper(), _SEV.get(sev, _SEV["low"]), mono=True)


def _bar(percent, color: str, height: int = 8) -> str:
    p = 0 if percent is None else max(0, min(100, int(round(percent))))
    fill = (
        f'<td width="{p}%" height="{height}" bgcolor="{color}" style="background:{color};font-size:0;line-height:0;border-radius:4px">&nbsp;</td>'
        if p > 0 else ""
    )
    rest = f'<td class="em-track" bgcolor="{SOFT}" style="background:{SOFT};font-size:0;line-height:0;border-radius:4px">&nbsp;</td>' if p < 100 else ""
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:4px;overflow:hidden"><tr>{fill}{rest}</tr></table>'
    )


def _section_head(index: str, title: str, blurb: str) -> str:
    return (
        f'<tr><td class="px" style="padding:34px 28px 14px">'
        f'<div style="font:600 11px/1 {F_MONO};letter-spacing:.14em;color:{ACCENT};text-transform:uppercase">{_e(index)}</div>'
        f'<div class="em-text" style="margin-top:9px;font:700 20px/1.25 {F_DISPLAY};color:{TEXT}">{_e(title)}</div>'
        f'<div class="em-faint" style="margin-top:5px;font:400 13px/1.5 {F_BODY};color:{FAINT}">{_e(blurb)}</div>'
        f'</td></tr>'
    )


def _card(inner: str, pad: str = "18px 20px") -> str:
    return (
        f'<tr><td class="px" style="padding:0 28px 14px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="em-card" '
        f'style="background:{CARD};border:1px solid {BORDER};border-radius:10px"><tr><td style="padding:{pad}">{inner}</td></tr></table>'
        f'</td></tr>'
    )


def _callout(text: str, kind: tuple) -> str:
    color, soft, cls = kind
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td class="{cls}" style="background:{soft};border-radius:8px;padding:12px 14px;font:500 13px/1.5 {F_BODY};color:{color}">{text}</td></tr></table>'
    )


def _eyebrow(text: str) -> str:
    return f'<div class="em-faint" style="font:600 10px/1.2 {F_MONO};letter-spacing:.12em;color:{FAINT};text-transform:uppercase">{_e(text)}</div>'


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------

def _header(data: dict) -> str:
    posture = data["posture"]
    kind = _POSTURE[posture["level"]]
    return f"""
<tr><td height="4" bgcolor="{ACCENT}" style="background:{ACCENT};border-radius:14px 14px 0 0;font-size:0;line-height:0">&nbsp;</td></tr>
<tr><td style="background:{INK}" bgcolor="{INK}">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td class="px" style="padding:24px 28px 0">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td valign="middle">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
            <td width="30" height="30" align="center" valign="middle" bgcolor="{ACCENT}" style="background:{ACCENT};border-radius:8px;font:700 16px/30px {F_DISPLAY};color:#FFFFFF">C</td>
            <td style="padding-left:10px;font:700 17px/1 {F_DISPLAY};color:#EDEEF0;letter-spacing:-.01em">Cortex</td>
          </tr></table>
        </td>
        <td align="right" valign="middle" style="font:600 10px/1 {F_MONO};letter-spacing:.16em;color:#8B909A;text-transform:uppercase">Weekly digest</td>
      </tr></table>
    </td></tr>
    <tr><td class="px" style="padding:30px 28px 6px">
      <div class="h1" style="font:700 30px/1.15 {F_DISPLAY};color:#FFFFFF;letter-spacing:-.02em">Infrastructure<br>week in review</div>
      <div style="margin-top:12px;font:400 14px/1.5 {F_BODY};color:#A9AEB8">{_e(data['window']['label'])} &nbsp;·&nbsp; {data['node_count']} node{'s' if data['node_count'] != 1 else ''} monitored</div>
    </td></tr>
    <tr><td class="px" style="padding:14px 28px 28px">
      <span style="display:inline-block;padding:6px 14px 6px 11px;border-radius:999px;background:rgba(255,255,255,.08);font:600 12px/1.3 {F_BODY};color:#EDEEF0">
        <span style="color:{kind[0]};font-size:14px;vertical-align:-1px">●</span>&nbsp; {_e(posture['label'])}
      </span>
    </td></tr>
  </table>
</td></tr>"""


def _ai_card(summary: dict) -> str:
    bullets = "".join(
        f'<tr><td valign="top" width="16" style="padding:5px 0 0;color:{ACCENT};font:700 12px/1.4 {F_BODY}">▸</td>'
        f'<td class="em-dim" style="padding:2px 0 7px;font:400 14px/1.6 {F_BODY};color:{DIM}">{_e(b)}</td></tr>'
        for b in summary["highlights"]
    )
    actions = ""
    if summary.get("actions"):
        items = "".join(
            f'<tr><td valign="top" width="22" style="padding:4px 0;font:600 12px/1.5 {F_MONO};color:{ACCENT}">{i}.</td>'
            f'<td class="em-text" style="padding:4px 0;font:500 13px/1.55 {F_BODY};color:{TEXT}">{_e(a)}</td></tr>'
            for i, a in enumerate(summary["actions"], 1)
        )
        actions = (
            f'<div style="margin-top:14px;padding-top:14px;border-top:1px solid {BORDER}" class="em-bd">'
            f'{_eyebrow("Recommended next steps")}<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:8px">{items}</table></div>'
        )
    source = (
        "Written by NVIDIA NIM from this week&#39;s data only"
        if summary.get("source") == "ai" else "Auto-generated from this week&#39;s data"
    )
    return (
        f'<tr><td class="px" style="padding:20px 28px 6px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="ai-card" '
        f'style="background:{ACCENT_SOFT};border:1px solid #F4D5CB;border-left:4px solid {ACCENT};border-radius:10px"><tr><td style="padding:18px 20px">'
        f'<div style="font:600 10px/1.2 {F_MONO};letter-spacing:.14em;color:{ACCENT};text-transform:uppercase">✦ &nbsp;Cortex AI summary</div>'
        f'<div class="em-text" style="margin:10px 0 12px;font:700 17px/1.4 {F_DISPLAY};color:{TEXT};letter-spacing:-.01em">{_e(summary["headline"])}</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{bullets}</table>{actions}'
        f'<div class="em-faint" style="margin-top:12px;font:400 11px/1.4 {F_BODY};color:{FAINT}">{source}</div>'
        f'</td></tr></table></td></tr>'
    )


def _kpi(label: str, value: str, sub: str, color: str) -> str:
    return (
        f'<td class="kpi" width="25%" valign="top" style="padding:0 4px 8px">'
        f'<table role="presentation" width="100%" height="100%" cellpadding="0" cellspacing="0" border="0" class="em-card" '
        f'style="background:{CARD};border:1px solid {BORDER};border-radius:10px;border-top:3px solid {color};height:100%"><tr><td valign="top" style="padding:13px 14px 12px">'
        f'{_eyebrow(label)}'
        f'<div class="em-text" style="margin-top:8px;font:700 26px/1 {F_DISPLAY};color:{TEXT};letter-spacing:-.02em">{value}</div>'
        f'<div style="margin-top:7px;font:400 12px/1.4 {F_BODY};color:{FAINT}" class="em-faint">{sub}</div>'
        f'</td></tr></table></td>'
    )


def _kpis(data: dict) -> str:
    cpu = data["capacity"]["fleet"].get("cpu") or {}
    ag, sec, rem = data["agents"]["anomalies"], data["security"], data["agents"]["remediation"]
    crit_cves = sec["cve_by_severity"].get("critical", 0)
    cells = "".join([
        _kpi("Fleet CPU", _pct(cpu.get("avg")), _delta(cpu.get("delta"), quiet_below=3) + " vs last wk", _level_color(cpu.get("avg"))),
        _kpi("Alerts", str(ag["total"]),
             _delta(ag["total"] - ag["prev_total"], unit="") + f" vs last wk", ACCENT),
        _kpi("Security", str(sec["total_signals"]),
             (f'<span style="color:{CRIT};font-weight:600">{crit_cves} critical CVE{"s" if crit_cves != 1 else ""}</span>' if crit_cves
              else "open signals" if sec["total_signals"] else "all clear"),
             CRIT if crit_cves else (WARN if sec["total_signals"] else OK)),
        _kpi("Fix proposals", str(rem["proposed"]), f"{rem['executed']} executed", PURPLE),
    ])
    return f'<tr><td class="px" style="padding:14px 24px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{cells}</tr></table></td></tr>'


# -- 01 capacity ------------------------------------------------------------

def _fleet_row(label: str, f: dict) -> str:
    avg = f.get("avg")
    peak = f.get("peak")
    peak_txt = f"peak {_pct(peak)} on {_e(f.get('peak_host'))}" if peak is not None and f.get("peak_host") else "no data"
    return (
        f'<tr><td style="padding:7px 0">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td class="em-text" width="86" style="font:600 13px/1.2 {F_BODY};color:{TEXT}">{_e(label)}</td>'
        f'<td style="padding-right:14px">{_bar(avg, _level_color(avg))}</td>'
        f'<td width="52" align="right" style="font:600 14px/1 {F_MONO};color:{_level_color(avg)}">{_pct(avg)}</td>'
        f'<td class="hide-sm" width="84" align="right" style="font:400 12px/1 {F_BODY}">{_delta(f.get("delta"), quiet_below=3)}</td>'
        f'</tr><tr><td></td><td colspan="3" class="em-faint" style="padding-top:4px;font:400 11px/1.3 {F_BODY};color:{FAINT}">{peak_txt}</td></tr>'
        f'</table></td></tr>'
    )


def _daily_chart(cap: dict, week_start: datetime) -> str:
    cpu = (cap["fleet"].get("cpu") or {}).get("daily") or [None] * 7
    mem = (cap["fleet"].get("memory") or {}).get("daily") or [None] * 7
    if not any(v is not None for v in cpu + mem):
        return ""
    max_h = 70
    # Scale to the busiest day so quiet fleets (all < 30%) still show shape,
    # but never exaggerate beyond 100%.
    scale_top = max(40.0, max(v for v in cpu + mem if v is not None))

    def bar(v, color):
        h = 2 if v is None else max(3, int(round(v / scale_top * max_h)))
        return f'<td valign="bottom"><div style="width:15px;height:{h}px;background:{color};border-radius:3px 3px 0 0;font-size:0;line-height:0">&nbsp;</div></td>'

    cols, labels = "", ""
    for i in range(7):
        day = (week_start + timedelta(days=i)).strftime("%a")
        cols += (
            f'<td align="center" valign="bottom" width="14%"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'{bar(cpu[i], ACCENT)}<td width="3"></td>{bar(mem[i], BLUE)}</tr></table></td>'
        )
        labels += (
            f'<td align="center" class="em-faint" style="padding-top:6px;font:500 11px/1.2 {F_BODY};color:{FAINT}">{day}</td>'
        )
    legend = (
        f'<span style="color:{ACCENT}">■</span> <span class="em-dim" style="color:{DIM}">CPU</span> &nbsp;&nbsp;'
        f'<span style="color:{BLUE}">■</span> <span class="em-dim" style="color:{DIM}">Memory</span>'
    )
    return (
        f'<div style="margin-top:18px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td>{_eyebrow("Daily fleet average")}</td><td align="right" style="font:500 11px/1 {F_BODY}">{legend}</td></tr></table>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px"><tr>{cols}</tr><tr>{labels}</tr></table>'
        f'<div class="em-faint" style="margin-top:8px;text-align:right;font:400 10px/1.2 {F_BODY};color:{FAINT}">Bars scaled 0–{scale_top:.0f}%</div></div>'
    )


def _node_table(nodes: list[dict]) -> str:
    if not nodes:
        return ""

    def cell(node, metric):
        avg, peak = node.get(f"{metric}_avg"), node.get(f"{metric}_peak")
        if avg is None:
            return f'<td align="right" style="padding:9px 0;font:400 13px/1 {F_BODY};color:{MUTED}">–</td>'
        return (
            f'<td align="right" style="padding:9px 0;border-top:1px solid {SOFT}" class="em-bd">'
            f'<div style="font:600 13px/1.2 {F_MONO};color:{_level_color(avg)}">{_pct(avg)}</div>'
            f'<div class="em-faint" style="font:400 10px/1.4 {F_BODY};color:{FAINT}">peak {_pct(peak)}</div></td>'
        )

    head = "".join(
        f'<td align="{a}" width="{w}" style="padding-bottom:8px">{_eyebrow(t)}</td>'
        for t, a, w in (("Node", "left", ""), ("CPU", "right", "64"), ("Memory", "right", "64"), ("Disk", "right", "64"))
    )
    rows = ""
    for n in nodes[:8]:
        rows += (
            f'<tr><td style="padding:9px 0;border-top:1px solid {SOFT}" class="em-bd">'
            f'<div class="em-text" style="font:600 13px/1.3 {F_BODY};color:{TEXT}">{_e(n["hostname"])}</div>'
            f'<div class="em-faint" style="font:400 11px/1.3 {F_BODY};color:{FAINT}">{_e(n["role"])}</div></td>'
            f'{cell(n, "cpu")}{cell(n, "memory")}{cell(n, "disk")}</tr>'
        )
    return (
        f'<div style="margin-top:18px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{head}</tr>{rows}</table></div>'
    )


def _forecast_block(cap: dict) -> str:
    warnings = cap["forecast_warnings"]
    if not warnings:
        body = _callout("<strong>No resource is projected to breach its threshold</strong> in the next 7 days.", (OK, OK_SOFT, "p-ok"))
    else:
        rows = ""
        for w in warnings:
            if w["already_breached"]:
                msg = f'<strong>{_e(w["hostname"])}</strong> {_e(_human_metric(w["metric"]).lower())} is already at {_pct(w["current"])} — past its {w["threshold"]:.0f}% threshold.'
                kind = (CRIT, CRIT_SOFT, "p-crit")
            else:
                msg = (f'<strong>{_e(w["hostname"])}</strong> {_e(_human_metric(w["metric"]).lower())} on track to hit {w["threshold"]:.0f}% '
                       f'in ~{w["eta_days"]} days (now {_pct(w["current"])}).')
                kind = (WARN, WARN_SOFT, "p-warn")
            rows += f'<tr><td style="padding-bottom:8px">{_callout(msg, kind)}</td></tr>'
        body = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>'
    return (
        f'<div style="margin-top:18px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">'
        f'{_eyebrow("Forecast · next 7 days")}<div style="margin-top:10px">{body}</div></div>'
    )


def _quota_block(cap: dict) -> str:
    alerts = cap["quota_alerts"]
    if not alerts:
        return ""
    rows = ""
    for q in alerts:
        kind = (CRIT, CRIT_SOFT, "p-crit") if q["severity"] == "critical" else (WARN, WARN_SOFT, "p-warn")
        what = "estimated monthly cost" if q["breach_type"] == "budget_cap" else _human_metric(q["resource"]).lower() + " quota"
        pct = None if q["ratio"] is None else q["ratio"] * 100
        rows += (
            f'<tr><td style="padding:6px 0;border-top:1px solid {SOFT}" class="em-bd">'
            f'<span class="em-text" style="font:600 13px/1.4 {F_BODY};color:{TEXT}">{_e(q["project"])}</span>'
            f'<span class="em-dim" style="font:400 13px/1.4 {F_BODY};color:{DIM}"> · {_e(what)} at {_pct(pct)}</span></td>'
            f'<td align="right" style="padding:6px 0;border-top:1px solid {SOFT}" class="em-bd">{_pill(q["severity"].upper(), kind, mono=True)}</td></tr>'
        )
    return (
        f'<div style="margin-top:18px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">'
        f'{_eyebrow("Quota & budget")}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px">{rows}</table></div>'
    )


def _capacity(data: dict, week_start: datetime) -> str:
    cap = data["capacity"]
    head = _section_head("01 · Capacity", "Capacity trend", "Fleet utilisation this week, compared with the week before.")
    if not cap["available"]:
        return head + _card(_callout("Capacity metrics were <strong>unavailable</strong> this week (Prometheus could not be reached). Forecast and quota data below may still apply.", (WARN, WARN_SOFT, "p-warn"))
                            + _forecast_block(cap) + _quota_block(cap))
    fleet = cap["fleet"]
    rows = "".join(_fleet_row(lbl, fleet[key]) for key, lbl in (("cpu", "CPU"), ("memory", "Memory"), ("disk", "Disk")) if key in fleet)
    inner = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>'
        + _daily_chart(cap, week_start) + _node_table(cap["nodes"]) + _forecast_block(cap) + _quota_block(cap)
    )
    return head + _card(inner)


# -- 02 security ------------------------------------------------------------

def _security(data: dict) -> str:
    sec = data["security"]
    head = _section_head("02 · Security", "Security posture", "Current findings from the continuous scan of every node.")
    if not sec["available"]:
        return head + _card(_callout("No security scan results were available yet.", (WARN, WARN_SOFT, "p-warn")))

    t = sec["totals"]
    tiles = [
        ("Permissive rules", t["risky_rules"], CRIT),
        ("Rule drift", t["drift_groups"], WARN),
        ("Vulnerable packages", t["cves"], CRIT),
        ("Port mismatches", t["port_mismatches"], WARN),
        ("Kernel (eBPF) alerts", t["ebpf_alerts"], CRIT),
        ("Auth anomalies", t["auth_signals"], WARN),
    ]

    def tile(label, n, color):
        c = OK if n == 0 else color
        return (
            f'<td class="stack" width="33%" valign="top" style="padding:0 4px 8px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="em-soft" style="background:{BG};border-radius:8px"><tr><td style="padding:12px 14px">'
            f'<div style="font:700 24px/1 {F_DISPLAY};color:{c}">{n}</div>'
            f'<div class="em-faint" style="margin-top:6px;font:500 11px/1.3 {F_BODY};color:{FAINT}">{_e(label)}</div></td></tr></table></td>'
        )

    grid = "".join(f"<tr>{''.join(tile(*x) for x in tiles[i:i + 3])}</tr>" for i in (0, 3))
    grid = f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 -4px;width:calc(100% + 8px)">{grid}</table>'

    if sec["total_signals"] == 0:
        status = _callout(f"<strong>All clear.</strong> No open signals across {sec['nodes_scanned']} scanned node(s).", (OK, OK_SOFT, "p-ok"))
    else:
        affected = ", ".join(_e(h) for h in sec["hosts_with_signal"][:5])
        status = _callout(f"<strong>{sec['total_signals']} open signal(s)</strong> on {sec['nodes_with_signal']} of {sec['nodes_scanned']} node(s): {affected}.", (WARN, WARN_SOFT, "p-warn"))

    cves = ""
    if sec["top_cves"]:
        rows = "".join(
            f'<tr><td style="padding:8px 0;border-top:1px solid {SOFT}" class="em-bd" width="82">{_sev_pill(c["severity"])}</td>'
            f'<td style="padding:8px 8px;border-top:1px solid {SOFT}" class="em-bd">'
            f'<div class="em-text" style="font:600 12px/1.3 {F_MONO};color:{TEXT}">{_e(c["cve_id"])}</div>'
            f'<div class="em-faint" style="font:400 11px/1.4 {F_BODY};color:{FAINT}">{_e(c["package"])} {_e(c["installed_version"])}'
            f'{" → fixed in " + _e(c["fixed_version"]) if c.get("fixed_version") else ""}</div></td>'
            f'<td align="right" class="em-dim" style="padding:8px 0;border-top:1px solid {SOFT};font:500 12px/1.3 {F_BODY};color:{DIM}">{_e(c["hostname"])}</td></tr>'
            for c in sec["top_cves"]
        )
        cves = (
            f'<div style="margin-top:14px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">{_eyebrow("Most severe vulnerabilities")}'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px">{rows}</table></div>'
        )

    reliability = ""
    if sec["scan_runs"]:
        reliability = (
            f'<div class="em-faint" style="margin-top:14px;font:400 12px/1.5 {F_BODY};color:{FAINT}">'
            f'Scanner reliability: {sec["scan_runs"]} passes this week, <strong style="color:{_level_color(100 - (sec["scan_ok_pct"] or 0))}">{_pct(sec["scan_ok_pct"])}</strong> completed cleanly.</div>'
        )
    return head + _card(f'{grid}<div style="margin-top:6px">{status}</div>{cves}{reliability}')


# -- 03 agents --------------------------------------------------------------

def _mini_stat(label: str, value: str, sub: str = "", cols: int = 3) -> str:
    return (
        f'<td class="stack" width="{100 // cols}%" valign="top" style="padding:0 4px 8px"><table role="presentation" width="100%" height="100%" cellpadding="0" cellspacing="0" border="0" class="em-soft" style="background:{BG};border-radius:8px;height:100%"><tr><td valign="top" style="padding:12px 14px">'
        f'{_eyebrow(label)}<div class="em-text" style="margin-top:7px;font:700 22px/1 {F_DISPLAY};color:{TEXT}">{value}</div>'
        f'<div class="em-faint" style="margin-top:6px;font:400 11px/1.4 {F_BODY};color:{FAINT}">{sub or "&nbsp;"}</div></td></tr></table></td>'
    )


def _agents(data: dict) -> str:
    ag, rem, act = data["agents"]["anomalies"], data["agents"]["remediation"], data["agents"]["activity"]
    head = _section_head("03 · Agents", "What the agents caught", "Anomalies detected, fixes proposed and questions answered.")

    avg_min = ag["avg_resolution_minutes"]
    ttr = "–" if avg_min is None else (f"{avg_min:.0f} min" if avg_min < 120 else f"{avg_min / 60:.1f} h")
    stats = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 -4px;width:calc(100% + 8px)"><tr>'
        + _mini_stat("Alerts detected", str(ag["total"]), _delta(ag["total"] - ag["prev_total"], unit="") + " vs last week")
        + _mini_stat("Resolved", f'{ag["resolved"]}<span class="em-faint" style="font:500 13px {F_BODY};color:{FAINT}"> / {ag["total"]}</span>', f"avg {ttr} to resolve")
        + _mini_stat("Still open", str(ag["still_open"]), "needs review" if ag["still_open"] else "nothing outstanding")
        + "</tr></table>"
    )

    sev = "".join(
        f'{_pill(f"{n} {k}", _SEV[k])} &nbsp;' for k, n in ag["by_severity"].items() if n
    )
    sev_line = f'<div style="margin-top:4px">{sev}</div>' if sev else ""

    incidents = ""
    if ag["notable"]:
        rows = "".join(
            f'<tr><td width="82" style="padding:8px 0;border-top:1px solid {SOFT}" class="em-bd">{_sev_pill(i["severity"])}</td>'
            f'<td style="padding:8px 8px;border-top:1px solid {SOFT}" class="em-bd">'
            f'<div class="em-text" style="font:600 13px/1.3 {F_BODY};color:{TEXT}">{_e(i["hostname"])}</div>'
            f'<div class="em-faint" style="font:400 11px/1.4 {F_BODY};color:{FAINT}">{_e(_human_metric(i["metric"]))} · value {_e(i["value"])}'
            f'{" · z " + _e(i["z_score"]) if i.get("z_score") is not None else ""}</div></td>'
            f'<td align="right" style="padding:8px 0;border-top:1px solid {SOFT}" class="em-bd">'
            f'{_pill("Resolved", (OK, OK_SOFT, "p-ok")) if i["resolved"] else _pill("Open", (WARN, WARN_SOFT, "p-warn"))}</td></tr>'
            for i in ag["notable"]
        )
        incidents = (
            f'<div style="margin-top:14px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">{_eyebrow("Notable incidents")}'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px">{rows}</table></div>'
        )
    elif ag["total"] == 0:
        incidents = f'<div style="margin-top:6px">{_callout("<strong>No anomalies detected</strong> this week — every metric stayed within its baseline.", (OK, OK_SOFT, "p-ok"))}</div>'

    remediation = (
        f'<div style="margin-top:14px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">{_eyebrow("Remediation agent")}'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:10px -4px 0;width:calc(100% + 8px)"><tr>'
        + _mini_stat("Proposed", str(rem["proposed"]), cols=4) + _mini_stat("Approved", str(rem["approved"]), cols=4)
        + _mini_stat("Executed", str(rem["executed"]), f'{rem["execution_failed"]} failed' if rem["execution_failed"] else "", cols=4)
        + _mini_stat("Rejected", str(rem["rejected"]), cols=4)
        + "</tr></table>"
    )
    if rem["recent"]:
        label = {"executed": ("Executed", (OK, OK_SOFT, "p-ok")), "approved": ("Approved", (BLUE, "#E8F0F9", "p-neutral")),
                 "execution_failed": ("Failed", (CRIT, CRIT_SOFT, "p-crit"))}
        rows = "".join(
            f'<tr><td style="padding:7px 0;border-top:1px solid {SOFT}" class="em-bd"><span class="em-text" style="font:600 13px/1.4 {F_BODY};color:{TEXT}">{_e(r["title"])}</span>'
            f'<span class="em-faint" style="font:400 12px/1.4 {F_BODY};color:{FAINT}">{" · " + _e(r["host"]) if r.get("host") else ""}{" · by " + _e(r["actor"]) if r.get("actor") else ""}</span></td>'
            f'<td align="right" style="padding:7px 0;border-top:1px solid {SOFT}" class="em-bd">{_pill(*label[r["event"]])}</td></tr>'
            for r in rem["recent"]
        )
        remediation += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:4px">{rows}</table>'
    remediation += "</div>"

    top_agents = ""
    if act["by_agent"]:
        chips = "".join(f'{_pill(f"{a['agent']} · {a['count']}", (FAINT, SOFT, "p-neutral"))} &nbsp;' for a in act["by_agent"])
        top_agents = (
            f'<div style="margin-top:14px;padding-top:16px;border-top:1px solid {BORDER}" class="em-bd">{_eyebrow("Copilot activity")}'
            f'<div class="em-dim" style="margin:8px 0 8px;font:400 13px/1.5 {F_BODY};color:{DIM}"><strong class="em-text" style="color:{TEXT}">{act["questions"]}</strong> question(s) answered '
            f'({_delta(act["questions"] - act["prev_questions"], unit="", neutral=True)} vs last week)'
            f'{f", {act["critic_flagged_pct"]:.0f}% flagged by the critic" if act["critic_flagged_pct"] else ""}.</div>{chips}</div>'
        )

    return head + _card(stats + sev_line + incidents + remediation + top_agents)


def _cta_and_footer(data: dict) -> str:
    base = (os.getenv("CORTEX_PUBLIC_URL") or "").rstrip("/")
    cta = ""
    if base.startswith(("http://", "https://")):
        cta = (
            f'<tr><td class="px" align="center" style="padding:20px 28px 6px">'
            f'<a href="{_e(base)}/dashboard" style="display:inline-block;padding:13px 26px;border-radius:8px;background:{ACCENT};color:#FFFFFF;'
            f'font:600 14px/1 {F_BODY}">Open Cortex dashboard &rarr;</a></td></tr>'
        )
    return f"""{cta}
<tr><td class="px" style="padding:26px 28px 30px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="em-bd" style="border-top:1px solid {BORDER};padding-top:18px;font:400 11px/1.65 {F_BODY};color:{FAINT}">
    <span class="em-dim" style="font-weight:600;color:{DIM}">Cortex</span> · automated weekly digest<br>
    Covers {_e(data['window']['label'])} (UTC). Generated {_e(data['generated_at'][:16].replace('T', ' '))} UTC from live platform data.<br>
    Hostnames, counts and CVE identifiers only — no credentials, IP addresses or log contents are included.<br>
    Change the schedule or recipient under <strong>Settings → Weekly digest</strong> in Cortex.
  </td></tr></table>
</td></tr>"""


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def subject_line(data: dict) -> str:
    return f"[Cortex] Weekly digest · {data['posture']['label']} · {data['window']['label']}"


def render_html(data: dict, summary: dict) -> str:
    week_start = datetime.fromisoformat(data["window"]["start"].rstrip("Z"))
    preheader = _e(summary["headline"])
    body = (
        _header(data) + _ai_card(summary) + _kpis(data)
        + _capacity(data, week_start) + _security(data) + _agents(data) + _cta_and_footer(data)
    )
    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_e(subject_line(data))}</title>
<style>{_STYLE}</style>
</head>
<body class="em-bg" style="margin:0;padding:0;background:{BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:{BG}">{preheader}&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="em-bg" bgcolor="{BG}" style="background:{BG}">
<tr><td align="center" style="padding:24px 12px 12px">
  <table role="presentation" class="shell em-bg" width="640" cellpadding="0" cellspacing="0" border="0" style="width:640px;max-width:640px">
    {body}
  </table>
</td></tr></table>
</body></html>"""


def render_text(data: dict, summary: dict) -> str:
    cap, sec = data["capacity"], data["security"]
    ag, rem, act = data["agents"]["anomalies"], data["agents"]["remediation"], data["agents"]["activity"]
    out: list[str] = [
        f"CORTEX WEEKLY DIGEST — {data['window']['label']}",
        f"Status: {data['posture']['label']}  ·  {data['node_count']} node(s) monitored",
        "",
        "SUMMARY",
        summary["headline"],
        *[f"  - {h}" for h in summary["highlights"]],
    ]
    if summary.get("actions"):
        out += ["", "Recommended next steps:", *[f"  {i}. {a}" for i, a in enumerate(summary["actions"], 1)]]

    out += ["", "01  CAPACITY TREND"]
    if cap["available"]:
        for key, label in (("cpu", "CPU"), ("memory", "Memory"), ("disk", "Disk")):
            f = cap["fleet"].get(key)
            if f:
                d = "" if f["delta"] is None else f" ({f['delta']:+.1f} pts vs last week)"
                out.append(f"  {label:<7} avg {_pct(f['avg'])}{d}, peak {_pct(f['peak'])} on {f['peak_host']}")
    else:
        out.append("  Capacity metrics were unavailable this week.")
    for w in cap["forecast_warnings"]:
        out.append(
            f"  ! {w['hostname']} {w['metric']}: " + ("already past threshold" if w["already_breached"] else f"hits {w['threshold']:.0f}% in ~{w['eta_days']} days")
        )
    if not cap["forecast_warnings"]:
        out.append("  No projected threshold breaches in the next 7 days.")
    for q in cap["quota_alerts"]:
        out.append(f"  ! {q['project']}: {q['resource']} at {_pct((q['ratio'] or 0) * 100)} ({q['severity']})")

    out += ["", "02  SECURITY POSTURE"]
    if sec["available"]:
        t = sec["totals"]
        out += [
            f"  {sec['total_signals']} open signal(s) across {sec['nodes_scanned']} node(s)",
            f"  permissive rules {t['risky_rules']} · drift {t['drift_groups']} · vulnerable packages {t['cves']} · "
            f"port mismatches {t['port_mismatches']} · eBPF alerts {t['ebpf_alerts']} · auth anomalies {t['auth_signals']}",
            *[f"  {c['severity'].upper():<8} {c['cve_id']} {c['package']} on {c['hostname']}" for c in sec["top_cves"]],
        ]
    else:
        out.append("  No security scan results available yet.")

    out += [
        "", "03  WHAT THE AGENTS CAUGHT",
        f"  Alerts detected: {ag['total']} (last week {ag['prev_total']}) · resolved {ag['resolved']} · still open {ag['still_open']}",
        f"  Remediation: {rem['proposed']} proposed · {rem['approved']} approved · {rem['executed']} executed · {rem['rejected']} rejected",
        f"  Copilot questions answered: {act['questions']}",
        *[f"  {i['severity'].upper():<8} {i['hostname']} {_human_metric(i['metric'])} ({'resolved' if i['resolved'] else 'open'})" for i in ag["notable"]],
        "", "—", "Cortex automated weekly digest. Manage under Settings → Weekly digest.",
    ]
    return "\n".join(out)
