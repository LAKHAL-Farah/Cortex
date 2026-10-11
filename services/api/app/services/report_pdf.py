"""PDF renderers for the three professional report variants (roadmap 4.11).

Pure rendering: every function takes the plain dict assembled by
``reports.collect_report_data`` and returns PDF bytes. No DB, no network --
so layout can be tested against hand-built data, and a missing section
(Prometheus down, no VM metrics) renders as an explicit "unavailable" note
instead of an empty page or a 500.

Variants (same visual language, different reader):
  executive -- 1-2 pages. Posture, headline KPIs, what needs a decision.
  financial -- cost, chargeback by department/project, budgets, savings.
  technical -- capacity per node, forecast, security, agents, VM sizing.

Everything uses ReportLab's built-in Helvetica (WinAnsi), so there are no font
files to ship; ``_t`` replaces characters outside that range (OpenStack names
can contain anything) rather than printing black boxes.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Iterable
from xml.sax.saxutils import escape as _xml_escape

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------- palette --
ACCENT = colors.HexColor("#1f4e79")
ACCENT_SOFT = colors.HexColor("#e8eef5")
INK = colors.HexColor("#1b1f24")
MUTED = colors.HexColor("#5b6670")
GRID = colors.HexColor("#c9d1da")
ZEBRA = colors.HexColor("#f4f6f9")
OK, WARN, CRIT = colors.HexColor("#2e7d32"), colors.HexColor("#d98200"), colors.HexColor("#c62828")
CHART_COLORS = [colors.HexColor(h) for h in ("#1f4e79", "#4f8fc0", "#9db8d2", "#d98200", "#6a4c93", "#2e7d32")]

_SEV_COLOR = {"critical": CRIT, "high": CRIT, "action": CRIT, "medium": WARN, "attention": WARN, "warning": WARN,
              "low": OK, "ok": OK, "normal": OK}
_RISK_COLOR = {"low": OK, "medium": WARN, "high": CRIT}
_POSTURE_COLOR = {"ok": OK, "attention": WARN, "action": CRIT}

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


# ---------------------------------------------------------------- helpers --
def _t(value: Any) -> str:
    """Escape for ReportLab's mini-markup and drop what Helvetica can't draw."""
    text = "-" if value is None else str(value)
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    return _xml_escape(text)


def _eur(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"\u20ac{value:,.{digits}f}"


def _pct(value: float | None, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}%"


def _num(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _gb(mb: float | None) -> str:
    if mb is None:
        return "n/a"
    g = mb / 1024.0
    return f"{g:g} GB" if g >= 1 else f"{mb:.0f} MB"


def _day(iso: str | None) -> str:
    if not iso:
        return "n/a"
    try:
        return datetime.fromisoformat(iso.replace("Z", "")).strftime("%d %b %Y")
    except ValueError:
        return str(iso)[:10]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK)
    return {
        "body": body,
        "small": ParagraphStyle("small", parent=body, fontSize=7.5, leading=10, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=body, fontSize=7.8, leading=9.6),
        "cell_r": ParagraphStyle("cell_r", parent=body, fontSize=7.8, leading=9.6, alignment=2),
        "h1": ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=colors.white),
        "sub": ParagraphStyle("sub", parent=body, fontSize=9, leading=12, textColor=colors.HexColor("#dbe6f1")),
        "h2": ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=ACCENT,
                             spaceBefore=12, spaceAfter=4, keepWithNext=1),
        "h3": ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=INK,
                             spaceBefore=6, spaceAfter=2, keepWithNext=1),
        "lead": ParagraphStyle("lead", parent=body, fontSize=11, leading=15, textColor=INK),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=10, bulletIndent=0, spaceAfter=2),
        "kpi_label": ParagraphStyle("kpi_label", parent=body, fontSize=7.2, leading=9, textColor=MUTED),
        "kpi_value": ParagraphStyle("kpi_value", parent=body, fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=INK),
        "kpi_sub": ParagraphStyle("kpi_sub", parent=body, fontSize=7, leading=8.5, textColor=MUTED),
    }


class _NumberedCanvas(rl_canvas.Canvas):
    """Two-pass canvas so the footer can say 'Page X of Y'."""

    footer_left = "Cortex"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        self.setStrokeColor(GRID)
        self.setLineWidth(0.4)
        self.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, 8 * mm, self.footer_left)
        self.drawRightString(PAGE_W - MARGIN, 8 * mm, f"Page {self._pageNumber} of {total}")


def _canvas_for(footer: str):
    return type("FooterCanvas", (_NumberedCanvas,), {"footer_left": footer})


# ------------------------------------------------------------ flowables --
def _banner(title: str, subtitle: str, st: dict) -> Table:
    t = Table([[Paragraph(_t(title), st["h1"])], [Paragraph(_t(subtitle), st["sub"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 12), ("BOTTOMPADDING", (0, 0), (0, 0), 1),
        ("TOPPADDING", (0, 1), (0, 1), 0), ("BOTTOMPADDING", (0, 1), (0, 1), 11),
    ]))
    return t


def _kpis(items: list[tuple[str, str, str | None]], st: dict, per_row: int = 4) -> Table:
    """[(label, value, sub)] as a grid of cards."""
    cells = []
    for label, value, sub in items:
        inner = [Paragraph(_t(label), st["kpi_label"]), Paragraph(_t(value), st["kpi_value"])]
        if sub:
            inner.append(Paragraph(_t(sub), st["kpi_sub"]))
        cells.append(inner)
    rows = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    for r in rows:
        r.extend([""] * (per_row - len(r)))
    w = CONTENT_W / per_row
    t = Table(rows, colWidths=[w] * per_row)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.4, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.4, GRID),
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT_SOFT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _badge(text: str, color, st: dict, width: float = 38 * mm) -> Table:
    p = ParagraphStyle("badge", parent=st["body"], fontName="Helvetica-Bold", fontSize=9, textColor=colors.white, alignment=1)
    t = Table([[Paragraph(_t(text), p)]], colWidths=[width])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color), ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _table(header: list[str], rows: list[list[Any]], widths: list[float], st: dict, right_from: int = 1,
           empty: str = "No data") -> Table:
    def cell(v, col):
        if isinstance(v, Paragraph):
            return v
        return Paragraph(_t(v), st["cell_r"] if col >= right_from else st["cell"])

    head_style = ParagraphStyle("th", parent=st["cell"], fontName="Helvetica-Bold", textColor=colors.white)
    head_style_r = ParagraphStyle("thr", parent=head_style, alignment=2)
    data = [[Paragraph(_t(h), head_style_r if i >= right_from else head_style) for i, h in enumerate(header)]]
    if rows:
        data += [[cell(v, i) for i, v in enumerate(r)] for r in rows]
    else:
        data.append([Paragraph(_t(empty), st["small"])] + [""] * (len(header) - 1))
    scale = CONTENT_W / sum(widths)
    t = Table(data, colWidths=[w * scale for w in widths], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if not rows:
        style.append(("SPAN", (0, 1), (-1, 1)))
    t.setStyle(TableStyle(style))
    return t


def _colored(text: str, color, st: dict, key: str = "cell") -> Paragraph:
    return Paragraph(f'<font color="{color.hexval().replace("0x", "#")}"><b>{_t(text)}</b></font>', st[key])


def _bullets(lines: Iterable[str], st: dict) -> list:
    return [Paragraph(_t(line), st["bullet"], bulletText="\u2022") for line in lines]


def _note(text: str, st: dict, color=WARN) -> Table:
    t = Table([[Paragraph(_t(text), st["small"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, color), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffaf0")),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    return t


def _unavailable(title: str, why: str, st: dict) -> list:
    return [Paragraph(_t(title), st["h2"]), _note(why, st, color=MUTED)]


def _stacked_bars(categories: list[str], series: list[tuple[str, list[float]]], width: float, height: float,
                  money: bool = True, horizontal: bool = True, stacked: bool = True) -> Drawing:
    d = Drawing(width, height)
    chart = HorizontalBarChart() if horizontal else VerticalBarChart()
    chart.x, chart.y = (52 if horizontal else 30), 28
    chart.width, chart.height = width - (52 if horizontal else 30) - 12, height - 44
    chart.data = [vals for _, vals in series]
    chart.categoryAxis.categoryNames = [c if len(c) <= 22 else c[:21] + "\u2026" for c in categories]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = GRID
    chart.valueAxis.gridStrokeWidth = 0.3
    if money:
        chart.valueAxis.labelTextFormat = lambda v: f"\u20ac{v:,.0f}"
    chart.groupSpacing = 6
    chart.barWidth = 9
    if stacked:
        chart.categoryAxis.style = "stacked"
    for i in range(len(series)):
        chart.bars[i].fillColor = CHART_COLORS[i % len(CHART_COLORS)]
        chart.bars[i].strokeColor = None
    if horizontal:
        chart.categoryAxis.reverseDirection = 1
    d.add(chart)
    legend = Legend()
    legend.x, legend.y = chart.x, height - 6
    legend.alignment = "right"
    legend.fontName, legend.fontSize = "Helvetica", 7
    legend.columnMaximum = 1
    legend.deltax, legend.dxTextSpace = 60, 4
    legend.boxAnchor = "nw"
    legend.colorNamePairs = [(CHART_COLORS[i % len(CHART_COLORS)], name) for i, (name, _) in enumerate(series)]
    d.add(legend)
    if not categories:
        d.add(String(width / 2, height / 2, "No data", textAnchor="middle", fontSize=8, fillColor=MUTED))
    return d


def _chart_height(n: int) -> float:
    return max(70.0, 44.0 + 17.0 * max(n, 1))


def _build(story: list, title: str, footer: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * mm,
                            bottomMargin=18 * mm, title=title, author="Cortex", subject=footer)
    doc.build(story, canvasmaker=_canvas_for(footer))
    return buf.getvalue()


# ---------------------------------------------------------- shared blocks --
def _posture_block(data: dict, st: dict, text: str | None = None) -> list:
    ops = data.get("operations")
    if not ops:
        return []
    posture = ops["posture"]
    body = text or "; ".join(posture["reasons"]) or "No open issues across capacity, security and alerts."
    return [
        Table(
            [[_badge(posture["label"], _POSTURE_COLOR.get(posture["level"], MUTED), st),
              Paragraph(_t(body), st["lead"] if text else st["body"])]],
            colWidths=[42 * mm, CONTENT_W - 42 * mm],
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, 0), 0)]),
        )
    ]


def _rightsizing_savings_table(rs: dict, st: dict, limit: int, detail: bool = False) -> list:
    out: list = []
    if not rs or not rs.get("available"):
        reason = (rs or {}).get("reason") or "VM-level utilisation is not available."
        return _unavailable("VM right-sizing", reason, st)
    summary = rs["summary"]
    out.append(Paragraph("VM right-sizing opportunities", st["h2"]))
    if rs.get("source") == "synthetic":
        out.append(_note("DEMO DATA: these VMs come from Cortex's synthetic test fleet, not from your cloud.", st, color=CRIT))
        out.append(Spacer(1, 4))
    out.append(Paragraph(
        _t(
            f"{summary['flagged']} of {summary['vms_analyzed']} analysed VMs are over-provisioned. "
            + (f"Resizing them would reclaim about {_eur(summary['estimated_monthly_savings_eur'])} per month "
               f"({_eur(summary['estimated_annual_savings_eur'], 0)} per year), of which "
               f"{_eur(summary['low_risk_monthly_savings_eur'])} per month is low-risk. "
               if summary.get("priced") else "Savings could not be priced (no unit rates yet). ")
            + f"Fleet efficiency score: {_num(summary['fleet_efficiency_score'], 0)}/100."
        ),
        st["body"],
    ))
    out.append(Spacer(1, 4))
    recs = rs["recommendations"][:limit]
    if detail:
        rows = []
        for r in recs:
            u = r["utilization"]
            rows.append([
                Paragraph(f"<b>{_t(r['name'])}</b><br/><font size=6.5 color='#5b6670'>{_t(r.get('project_name') or '')}</font>", st["cell"]),
                f"{r['current']['vcpus']} vCPU / {_gb(r['current']['ram_mb'])}",
                f"{r['recommended']['vcpus']} vCPU / {_gb(r['recommended']['ram_mb'])}",
                f"{_pct(u['cpu_p95'])} / {_pct(u['cpu_max'])}",
                f"{_pct(u['mem_p95'])} / {_pct(u['mem_max'])}",
                _eur(r["estimated_monthly_savings_eur"]),
                _colored(r["risk"]["level"].upper(), _RISK_COLOR[r["risk"]["level"]], st, "cell_r"),
                _num(r["efficiency_score"], 0),
            ])
        out.append(_table(["VM", "Current", "Suggested", "CPU p95/max", "Mem p95/max", "Saving/mo", "Risk", "Eff."],
                          rows, [30, 24, 24, 20, 20, 17, 13, 10], st, right_from=3, empty="No over-provisioned VMs found."))
    else:
        rows = [[r["name"], f"{r['current']['vcpus']}c/{_gb(r['current']['ram_mb'])}  to  {r['recommended']['vcpus']}c/{_gb(r['recommended']['ram_mb'])}",
                 _eur(r["estimated_monthly_savings_eur"]),
                 _colored(r["risk"]["level"].upper(), _RISK_COLOR[r["risk"]["level"]], st, "cell_r")] for r in recs]
        out.append(_table(["VM", "Change", "Saving / month", "Risk"], rows, [40, 50, 25, 15], st, right_from=2,
                          empty="No over-provisioned VMs found."))
    out.append(Spacer(1, 3))
    out.append(Paragraph(_t(
        "Recommendations only - Cortex never resizes a VM on its own. Savings are reclaimable run-rate at the cloud's "
        "unit rates; a fixed monthly bill only falls when the freed capacity is removed or reused."), st["small"]))
    return out


# ---------------------------------------------------------------- variants --
def render_executive(data: dict) -> bytes:
    st = _styles()
    fin, ops, rs = data.get("finance"), data.get("operations"), data.get("rightsizing")
    story: list = [
        _banner("Executive summary", f"Private cloud status \u2022 {data['period_label']} \u2022 generated {_day(data['generated_at'])}", st),
        Spacer(1, 8),
    ]
    summary = data.get("summary")
    story += _posture_block(data, st, text=summary["headline"] if summary else None)
    if summary:
        story += [Spacer(1, 6)] + _bullets(summary.get("highlights", []), st)

    kpis: list[tuple[str, str, str | None]] = []
    if fin:
        t = fin["totals"]
        kpis += [
            ("Cost charged to projects", _eur(t["fully_loaded_eur"], 0), data["period_label"]),
            ("Projected month-end" if fin["is_current_period"] else "Fixed bill (metered)",
             _eur(t["projected_month_end_eur"] if fin["is_current_period"] else t["cloud_bill_metered_eur"], 0), None),
            ("Idle capacity cost", _eur(t["idle_eur"], 0), "paid for, not reserved"),
        ]
    if rs and rs.get("available") and rs["summary"].get("priced"):
        kpis.append(("Reclaimable via right-sizing", _eur(rs["summary"]["estimated_monthly_savings_eur"], 0) + "/mo",
                     f"{rs['summary']['flagged']} VMs flagged"))
    else:
        kpis.append(("Reclaimable via right-sizing", "n/a", "no VM metrics"))
    if ops:
        a, s = ops["agents"]["anomalies"], ops["security"]
        kpis += [
            ("Open alerts", str(a["still_open"]), f"{a['total']} detected this week"),
            ("Critical CVEs", str(s["cve_by_severity"].get("critical", 0)) if s["available"] else "n/a", "unpatched"),
            ("Resolved by agents", str(a["auto_resolved"]), "without intervention"),
            ("Fixes executed", str(ops["agents"]["remediation"]["executed"]), "approved remediations"),
        ]
    if kpis:
        story += [Spacer(1, 8), _kpis(kpis, st)]

    actions = (summary or {}).get("actions") or []
    if actions:
        story += [Paragraph("Decisions and actions", st["h2"])] + _bullets(actions, st)

    if fin and fin["departments"]:
        deps = fin["departments"][:8]
        story += [Paragraph("Where the money goes", st["h2"]),
                  _stacked_bars([d["department"] for d in deps],
                                [("Direct", [d["direct_eur"] or 0 for d in deps]),
                                 ("Platform", [d["platform_eur"] or 0 for d in deps]),
                                 ("Idle", [d["idle_eur"] or 0 for d in deps])],
                                CONTENT_W, _chart_height(len(deps)))]
    story += _rightsizing_savings_table(rs, st, limit=5)
    if ops is None:
        story += _unavailable("Operations", "Operational data could not be collected for this report.", st)
    story += _data_notes(data, st)
    return _build(story, f"Cortex executive summary {data['period']}", f"Cortex \u2022 Executive summary \u2022 {data['period_label']}")


def render_financial(data: dict) -> bytes:
    st = _styles()
    fin, rs = data.get("finance"), data.get("rightsizing")
    story: list = [
        _banner("Financial report", f"Showback & chargeback \u2022 {data['period_label']} \u2022 generated {_day(data['generated_at'])}", st),
        Spacer(1, 8),
    ]
    if not fin:
        story += _unavailable("Cost data", "Cost metering data could not be read for this period.", st)
        story += _data_notes(data, st)
        return _build(story, f"Cortex financial report {data['period']}", f"Cortex \u2022 Financial report \u2022 {data['period_label']}")

    t = fin["totals"]
    story.append(_kpis([
        ("Fixed cloud bill (metered)", _eur(t["cloud_bill_metered_eur"]), f"{fin['hours_metered']} h metered"),
        ("Charged to projects", _eur(t["fully_loaded_eur"]), "direct + platform + idle"),
        ("Direct (showback)", _eur(t["direct_eur"]), "reserved resources"),
        ("Not recovered", _eur(t["unrecovered_eur"]), "bill minus charges"),
        ("Platform overhead", _eur(t["platform_eur"]), "shared nodes"),
        ("Idle capacity", _eur(t["idle_eur"]), "unreserved"),
        ("Projected month-end", _eur(t["projected_month_end_eur"]), "at current allocation")
        if fin["is_current_period"] else ("Period status", "Closed", "actuals, not a projection"),
        ("Metering coverage", _pct(fin["coverage_pct"], 1), None),
    ], st))
    if fin["coverage_pct"] is not None and fin["coverage_pct"] < 99:
        story += [Spacer(1, 4), _note(
            f"Cortex metered only {fin['coverage_pct']}% of the hours in this period, so totals cover metered hours only.", st)]

    deps = fin["departments"]
    story.append(Paragraph("Cost by department", st["h2"]))
    if deps:
        story.append(_stacked_bars([d["department"] for d in deps[:8]],
                                   [("Direct", [d["direct_eur"] or 0 for d in deps[:8]]),
                                    ("Platform", [d["platform_eur"] or 0 for d in deps[:8]]),
                                    ("Idle", [d["idle_eur"] or 0 for d in deps[:8]])],
                                   CONTENT_W, _chart_height(len(deps[:8]))))
    story.append(_table(
        ["Department", "Projects", "Direct", "Platform", "Idle", "Total charge", "Share"],
        [[d["department"], d["projects"], _eur(d["direct_eur"]), _eur(d["platform_eur"]), _eur(d["idle_eur"]),
          _eur(d["fully_loaded_eur"]), _pct(d["share_pct"], 1)] for d in deps],
        [34, 14, 20, 20, 18, 22, 12], st, empty="No metered usage in this period."))

    story.append(Paragraph("Cost by project", st["h2"]))
    rows = []
    for p in fin["projects"]:
        used = p["budget_used_ratio"]
        used_cell = "-" if used is None else _colored(f"{used * 100:.0f}%", CRIT if used >= 0.95 else WARN if used >= 0.8 else OK, st, "cell_r")
        rows.append([Paragraph(f"<b>{_t(p['project_name'])}</b><br/><font size=6.5 color='#5b6670'>{_t(p['department'])}</font>", st["cell"]),
                     f"{p['avg_vcpus']:g} / {p['avg_ram_gb']:g} GB", _eur(p["direct_eur"]), _eur((p["platform_eur"] or 0) + (p["idle_eur"] or 0)),
                     _eur(p["fully_loaded_eur"]), _eur(p["projected_month_end_eur"]) if fin["is_current_period"] else "-", used_cell])
    story.append(_table(["Project", "Avg vCPU / RAM", "Direct", "Platform + idle", "Total charge", "Projected", "Budget used"],
                        rows, [34, 22, 18, 20, 20, 20, 14], st, empty="No metered usage in this period."))

    over = [p for p in fin["projects"] if p["budget_used_ratio"] is not None and p["budget_used_ratio"] >= 0.8]
    if over:
        story += [Paragraph("Budget watch", st["h3"])] + _bullets(
            [f"{p['project_name']}: {p['budget_used_ratio'] * 100:.0f}% of its {_eur(p['budget_eur'], 0)} monthly budget used" for p in over], st)

    story += _rightsizing_savings_table(rs, st, limit=10)
    story.append(Paragraph("How these numbers are computed", st["h2"]))
    story += _bullets(fin["method"], st)
    story += _data_notes(data, st)
    return _build(story, f"Cortex financial report {data['period']}", f"Cortex \u2022 Financial report \u2022 {data['period_label']}")


def render_technical(data: dict) -> bytes:
    st = _styles()
    ops, rs = data.get("operations"), data.get("rightsizing")
    story: list = [
        _banner("Technical report", f"Capacity, security and operations \u2022 {data['period_label']} \u2022 generated {_day(data['generated_at'])}", st),
        Spacer(1, 8),
    ]
    if not ops:
        story += _unavailable("Operations", "Operational data could not be collected for this report.", st)
    else:
        story += _posture_block(data, st)
        story.append(Paragraph(_t(f"Operational window: {ops['window']['label']} \u2022 {ops['node_count']} monitored node(s)"), st["small"]))

        cap = ops["capacity"]
        story.append(Paragraph("Capacity", st["h2"]))
        if cap["available"]:
            f = cap["fleet"]
            story.append(_table(
                ["Fleet metric", "Average", "vs last week", "Peak", "Peak host"],
                [[{"cpu": "CPU", "memory": "Memory", "disk": "Disk"}.get(m, m), _pct(v.get("avg")),
                  "n/a" if v.get("delta") is None else f"{v['delta']:+.1f} pts", _pct(v.get("peak")), v.get("peak_host") or "-"]
                 for m, v in f.items()], [22, 16, 20, 16, 30], st, right_from=1))
            nodes = cap["nodes"][:12]
            if nodes:
                story.append(Spacer(1, 6))
                story.append(_stacked_bars([n["hostname"] for n in nodes],
                                           [("CPU avg %", [n.get("cpu_avg") or 0 for n in nodes]),
                                            ("Memory avg %", [n.get("memory_avg") or 0 for n in nodes]),
                                            ("Disk avg %", [n.get("disk_avg") or 0 for n in nodes])],
                                           CONTENT_W, _chart_height(len(nodes) * 2), money=False, stacked=False))
                story.append(_table(
                    ["Node", "Role", "CPU avg/peak", "Mem avg/peak", "Disk avg/peak"],
                    [[n["hostname"], n.get("role") or "-", f"{_pct(n.get('cpu_avg'))} / {_pct(n.get('cpu_peak'))}",
                      f"{_pct(n.get('memory_avg'))} / {_pct(n.get('memory_peak'))}", f"{_pct(n.get('disk_avg'))} / {_pct(n.get('disk_peak'))}"]
                     for n in cap["nodes"]], [26, 16, 24, 24, 24], st, right_from=2))
        else:
            story.append(_note("Prometheus capacity metrics were unavailable for this window.", st, color=MUTED))
        if cap["forecast_warnings"]:
            story.append(Paragraph("Projected threshold breaches", st["h3"]))
            story.append(_table(["Host", "Metric", "Now", "Threshold", "ETA"],
                                [[w["hostname"], w["metric"], _pct(w.get("current")), _pct(w.get("threshold")),
                                  "already past" if w["already_breached"] else f"~{w['eta_days']} d"] for w in cap["forecast_warnings"]],
                                [30, 16, 14, 16, 18], st, right_from=2))
        if cap["quota_alerts"]:
            story.append(Paragraph("Quota and budget alerts", st["h3"]))
            story.append(_table(["Project", "Type", "Resource", "Used", "Severity"],
                                [[q["project"], q["breach_type"].replace("_", " "), q["resource"].replace("_", " "),
                                  _pct((q["ratio"] or 0) * 100), _colored(q["severity"], _SEV_COLOR.get(q["severity"], MUTED), st, "cell_r")]
                                 for q in cap["quota_alerts"]], [28, 18, 28, 12, 14], st, right_from=3))

        sec = ops["security"]
        story.append(Paragraph("Security posture", st["h2"]))
        if sec["available"]:
            tot, cs = sec["totals"], sec["cve_by_severity"]
            story.append(_kpis([
                ("Nodes scanned", str(sec["nodes_scanned"]), f"{sec['nodes_with_signal']} with signals"),
                ("CVEs", str(tot["cves"]), f"{cs.get('critical', 0)} crit \u2022 {cs.get('high', 0)} high"),
                ("Risky SG rules", str(tot["risky_rules"]), f"{tot['drift_groups']} drifted groups"),
                ("Port mismatches", str(tot["port_mismatches"]), f"{tot['ebpf_alerts']} kernel alerts"),
            ], st))
            if sec["top_cves"]:
                story.append(Spacer(1, 4))
                story.append(_table(["CVE", "Severity", "Package", "Installed", "Fixed in", "Host"],
                                    [[c["cve_id"], _colored(c["severity"], _SEV_COLOR.get(c["severity"], MUTED), st), c.get("package"),
                                      c.get("installed_version"), c.get("fixed_version") or "-", c.get("hostname")] for c in sec["top_cves"]],
                                    [24, 14, 22, 20, 20, 20], st, right_from=99))
            if sec.get("scan_ok_pct") is not None:
                story.append(Paragraph(_t(f"Scan reliability: {sec['scan_ok_pct']:.0f}% of {sec['scan_runs']} scan run{'s' if sec['scan_runs'] != 1 else ''} completed cleanly."), st["small"]))
        else:
            story.append(_note("No security scan results are available yet.", st, color=MUTED))

        ag, rem, act = ops["agents"]["anomalies"], ops["agents"]["remediation"], ops["agents"]["activity"]
        story.append(Paragraph("Anomalies and agent activity", st["h2"]))
        story.append(_kpis([
            ("Alerts detected", str(ag["total"]), f"previous week: {ag['prev_total']}"),
            ("Still open", str(ag["still_open"]), None),
            ("Auto-resolved", str(ag["auto_resolved"]), f"{ag['manually_resolved']} manually"),
            ("Avg time to resolve", "n/a" if ag["avg_resolution_minutes"] is None else f"{ag['avg_resolution_minutes']:.0f} min", None),
        ], st))
        if ag["notable"]:
            story.append(Spacer(1, 4))
            story.append(_table(["Host", "Metric", "Severity", "Value", "z-score", "State"],
                                [[n["hostname"], n["metric"], _colored(n["severity"], _SEV_COLOR.get(n["severity"], MUTED), st), _num(n["value"], 2),
                                  _num(n["z_score"]), "resolved" if n["resolved"] else "open"] for n in ag["notable"]],
                                [24, 24, 14, 14, 12, 14], st, right_from=3))
        story.append(Spacer(1, 4))
        story.append(Paragraph(_t(
            f"Remediation: {rem['proposed']} proposed, {rem['approved']} approved, {rem['rejected']} rejected, "
            f"{rem['executed']} executed, {rem['execution_failed']} failed. Copilot answered {act['questions']} question(s) "
            f"({act['critic_flagged_pct']}% flagged by the critic, {act['degraded_pct']}% degraded)."), st["body"]))
        if rem["recent"]:
            story.append(_table(["Event", "Action", "Host", "By"],
                                [[e["event"].replace("_", " "), e["title"], e.get("host") or "-", e.get("actor") or "-"] for e in rem["recent"]],
                                [16, 50, 22, 20], st, right_from=99))

    story += _rightsizing_savings_table(rs, st, limit=25, detail=True)
    if rs and rs.get("available"):
        s = rs["summary"]
        cls = s["by_classification"]
        story.append(Paragraph(_t(
            f"Classification: {cls['idle']} idle, {cls['underutilized']} under-utilised, {cls['right_sized']} right-sized, "
            f"{cls['saturated']} saturated (may need more resources), {cls['insufficient_data']} with too little history. "
            f"Flavor source: {'Nova flavors' if rs.get('flavor_catalog') == 'nova' else 'generic size ladder - map to your closest flavor'}."), st["small"]))
        risky = [r for r in rs["recommendations"] if r["risk"]["level"] != "low"][:6]
        if risky:
            story.append(Paragraph("Risk notes", st["h3"]))
            story += _bullets([f"{r['name']} ({r['risk']['level']}): " + "; ".join(r["risk"]["reasons"][:3]) for r in risky], st)
    story += _data_notes(data, st)
    return _build(story, f"Cortex technical report {data['period']}", f"Cortex \u2022 Technical report \u2022 {data['period_label']}")


def _data_notes(data: dict, st: dict) -> list:
    notes = data.get("warnings") or []
    if not notes:
        return []
    return [Paragraph("Data notes", st["h3"])] + _bullets(notes, st)


RENDERERS = {"executive": render_executive, "financial": render_financial, "technical": render_technical}
