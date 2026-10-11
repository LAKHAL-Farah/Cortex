"""Showback / chargeback reports and the live FinOps overview.

Reports are *metered*, not estimated: a month's cost for a project is the sum
of its hourly `FinopsProjectSample` rows, each priced at the run-rate in force
during that hour (see models.FinopsProjectSample). So a mid-month quota
change, a new hypervisor, or a revised monthly bill changes the cost from that
hour on and leaves earlier hours alone.

Showback vs chargeback: both are in the same report.
- `direct` is the showback figure -- what the project's own reservations cost.
- `fully_loaded` (= direct + platform_share + idle_share) is the chargeback
  figure -- the project's slice of the entire fixed monthly bill, so that
  summing every project recovers the bill.

Coverage matters: if Cortex has only been metering for part of the month the
report says so (`coverage_pct`) instead of presenting a partial sum as a full
month.
"""
from __future__ import annotations

import csv
import io
import re
from xml.sax.saxutils import escape as _xml_escape
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from . import finops

_PERIOD_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse_period(period: str | None, now: datetime) -> tuple[int, int]:
    if not period:
        return now.year, now.month
    m = _PERIOD_RE.match(period)
    if not m:
        raise ValueError("period must look like YYYY-MM")
    return int(m.group(1)), int(m.group(2))


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def _round(value: float | None, ndigits: int = 2) -> float | None:
    return None if value is None else round(value, ndigits)


def _resolve_department(
    project_id: str, project_name: str, sample_department: str | None, settings: dict, env_departments: dict
) -> str:
    row = settings.get(project_id)
    if row is not None and row.department and row.department.strip():
        return row.department.strip()
    mapped = env_departments.get(project_id) or env_departments.get(project_name)
    if mapped:
        return mapped
    return sample_department or finops.UNASSIGNED_DEPARTMENT


def _resolve_budget(project_id: str, project_name: str, settings: dict, env_budgets: dict) -> float | None:
    row = settings.get(project_id)
    if row is not None and row.monthly_budget_eur and row.monthly_budget_eur > 0:
        return float(row.monthly_budget_eur)
    value = env_budgets.get(project_id) or env_budgets.get(project_name)
    return float(value) if value and value > 0 else None


def available_periods(db: Session) -> list[str]:
    seen: set[str] = set()
    for (ts,) in db.query(models.FinopsCloudSample.sampled_at).all():
        seen.add(f"{ts.year:04d}-{ts.month:02d}")
    return sorted(seen, reverse=True)


def build_report(db: Session, period: str | None = None, now: datetime | None = None) -> dict:
    # Imported lazily: quota_budget_monitor pulls in openstacksdk, which the
    # pure report path otherwise doesn't need at import time.
    from .quota_budget_monitor import _load_project_budgets

    now = now or datetime.utcnow()
    year, month = parse_period(period, now)
    start, end = _month_bounds(year, month)
    hours_in_month = finops.hours_in_month(year, month)
    is_current = start <= now < end
    elapsed_hours = (
        max(int((min(now, end) - start).total_seconds() // 3600) + 1, 1) if now >= start else 0
    )

    settings = {s.project_id: s for s in db.query(models.ProjectFinopsSetting).all()}
    env_budgets = _load_project_budgets()
    env_departments = finops.load_project_departments()

    samples = (
        db.query(models.FinopsProjectSample)
        .filter(models.FinopsProjectSample.sampled_at >= start, models.FinopsProjectSample.sampled_at < end)
        .order_by(models.FinopsProjectSample.sampled_at)
        .all()
    )
    cloud_samples = (
        db.query(models.FinopsCloudSample)
        .filter(models.FinopsCloudSample.sampled_at >= start, models.FinopsCloudSample.sampled_at < end)
        .order_by(models.FinopsCloudSample.sampled_at)
        .all()
    )

    per_project: dict[str, dict] = {}
    latest_bucket = max((s.sampled_at for s in samples), default=None)
    for smp in samples:
        agg = per_project.setdefault(
            smp.project_id,
            {
                "project_id": smp.project_id,
                "project_name": smp.project_name,
                "sample_department": smp.department,
                "hours": 0,
                "vcpu_hours": 0.0,
                "ram_gb_hours": 0.0,
                "storage_gb_hours": 0.0,
                "compute": 0.0,
                "storage": 0.0,
                "network": 0.0,
                "platform": 0.0,
                "idle": 0.0,
                "run_rate_direct": 0.0,
                "run_rate_fully_loaded": 0.0,
            },
        )
        agg["project_name"] = smp.project_name
        agg["sample_department"] = smp.department
        agg["hours"] += 1
        agg["vcpu_hours"] += smp.vcpus
        agg["ram_gb_hours"] += smp.ram_mb / 1024.0
        agg["storage_gb_hours"] += smp.gigabytes
        agg["compute"] += smp.compute_eur_month / hours_in_month
        agg["storage"] += smp.storage_eur_month / hours_in_month
        agg["network"] += smp.network_eur_month / hours_in_month
        agg["platform"] += smp.platform_eur_month / hours_in_month
        agg["idle"] += smp.idle_eur_month / hours_in_month
        if smp.sampled_at == latest_bucket:
            direct_rate = smp.compute_eur_month + smp.storage_eur_month + smp.network_eur_month
            agg["run_rate_direct"] = direct_rate
            agg["run_rate_fully_loaded"] = direct_rate + smp.platform_eur_month + smp.idle_eur_month

    remaining_fraction = max((end - now).total_seconds() / 3600.0, 0.0) / hours_in_month if is_current else 0.0

    projects: list[dict] = []
    for agg in per_project.values():
        direct = agg["compute"] + agg["storage"] + agg["network"]
        fully_loaded = direct + agg["platform"] + agg["idle"]
        budget = _resolve_budget(agg["project_id"], agg["project_name"], settings, env_budgets)
        # Only a project still present in the latest sample has a live
        # run-rate to project forward; a project deleted mid-month stops
        # accruing.
        projected = None
        if is_current:
            projected = fully_loaded + agg["run_rate_fully_loaded"] * remaining_fraction
        hours = agg["hours"] or 1
        projects.append(
            {
                "project_id": agg["project_id"],
                "project_name": agg["project_name"],
                "department": _resolve_department(
                    agg["project_id"], agg["project_name"], agg["sample_department"], settings, env_departments
                ),
                "hours_metered": agg["hours"],
                "avg_vcpus": _round(agg["vcpu_hours"] / hours),
                "avg_ram_gb": _round(agg["ram_gb_hours"] / hours),
                "avg_storage_gb": _round(agg["storage_gb_hours"] / hours),
                "compute_eur": _round(agg["compute"]),
                "storage_eur": _round(agg["storage"]),
                "network_eur": _round(agg["network"]),
                "direct_eur": _round(direct),
                "platform_eur": _round(agg["platform"]),
                "idle_eur": _round(agg["idle"]),
                "fully_loaded_eur": _round(fully_loaded),
                "projected_month_end_eur": _round(projected),
                "budget_eur": _round(budget),
                "budget_used_ratio": _round(direct / budget, 4) if budget else None,
            }
        )
    projects.sort(key=lambda p: p["fully_loaded_eur"] or 0.0, reverse=True)

    total_fully_loaded = sum(p["fully_loaded_eur"] or 0.0 for p in projects)
    for p in projects:
        p["share_pct"] = _round((p["fully_loaded_eur"] or 0.0) / total_fully_loaded * 100, 1) if total_fully_loaded else 0.0

    dept_map: dict[str, dict] = defaultdict(
        lambda: {"projects": 0, "direct_eur": 0.0, "platform_eur": 0.0, "idle_eur": 0.0, "fully_loaded_eur": 0.0, "budget_eur": 0.0}
    )
    for p in projects:
        d = dept_map[p["department"]]
        d["projects"] += 1
        d["direct_eur"] += p["direct_eur"] or 0.0
        d["platform_eur"] += p["platform_eur"] or 0.0
        d["idle_eur"] += p["idle_eur"] or 0.0
        d["fully_loaded_eur"] += p["fully_loaded_eur"] or 0.0
        d["budget_eur"] += p["budget_eur"] or 0.0
    departments = [
        {
            "department": name,
            "projects": d["projects"],
            "direct_eur": _round(d["direct_eur"]),
            "platform_eur": _round(d["platform_eur"]),
            "idle_eur": _round(d["idle_eur"]),
            "fully_loaded_eur": _round(d["fully_loaded_eur"]),
            "budget_eur": _round(d["budget_eur"]) if d["budget_eur"] else None,
            "share_pct": _round(d["fully_loaded_eur"] / total_fully_loaded * 100, 1) if total_fully_loaded else 0.0,
        }
        for name, d in dept_map.items()
    ]
    departments.sort(key=lambda d: d["fully_loaded_eur"] or 0.0, reverse=True)

    # Cloud-level reconciliation: what the fixed bill cost for the hours we
    # actually metered, vs what the projects together were charged for it.
    bill_metered = sum(c.monthly_cost_eur / hours_in_month for c in cloud_samples)
    recovered = sum((p["compute_eur"] or 0.0) + (p["storage_eur"] or 0.0) + (p["platform_eur"] or 0.0) + (p["idle_eur"] or 0.0) for p in projects)
    metered_hours = len(cloud_samples)
    coverage = min(metered_hours / elapsed_hours, 1.0) if elapsed_hours else 0.0

    return {
        "period": f"{year:04d}-{month:02d}",
        "period_start": start.isoformat() + "Z",
        "period_end": end.isoformat() + "Z",
        "generated_at": now.isoformat() + "Z",
        "currency": finops.CURRENCY,
        "is_current_period": is_current,
        "hours_in_month": hours_in_month,
        "hours_metered": metered_hours,
        "coverage_pct": _round(coverage * 100, 1),
        "totals": {
            "direct_eur": _round(sum(p["direct_eur"] or 0.0 for p in projects)),
            "platform_eur": _round(sum(p["platform_eur"] or 0.0 for p in projects)),
            "idle_eur": _round(sum(p["idle_eur"] or 0.0 for p in projects)),
            "fully_loaded_eur": _round(total_fully_loaded),
            "projected_month_end_eur": _round(sum(p["projected_month_end_eur"] or 0.0 for p in projects)) if is_current else None,
            "cloud_bill_metered_eur": _round(bill_metered),
            "unrecovered_eur": _round(max(bill_metered - recovered, 0.0)),
        },
        "departments": departments,
        "projects": projects,
        "available_periods": available_periods(db),
        "method": [
            "Cost is metered hourly from what each project has reserved (flavor vCPU/RAM and volume GB), "
            "priced at the unit rates the cloud's fixed monthly cost works out to per sellable unit.",
            "Direct (showback) = the project's reserved resources at those rates.",
            "Fully loaded (chargeback) = direct + share of shared platform nodes + share of unreserved (idle) "
            "capacity, split pro rata to direct cost, so all projects together recover the monthly bill.",
            "This is an internal allocation of a fixed bill, not an external invoice.",
        ],
    }


# ------------------------------------------------------------- renderers --
def render_csv(report: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "period", "project_id", "project", "department", "hours_metered", "avg_vcpus", "avg_ram_gb",
            "avg_storage_gb", "direct_eur", "platform_share_eur", "idle_share_eur", "fully_loaded_eur",
            "share_pct", "budget_eur", "budget_used_ratio",
        ]
    )
    for p in report["projects"]:
        w.writerow(
            [
                report["period"], p["project_id"], p["project_name"], p["department"], p["hours_metered"],
                p["avg_vcpus"], p["avg_ram_gb"], p["avg_storage_gb"], p["direct_eur"], p["platform_eur"],
                p["idle_eur"], p["fully_loaded_eur"], p["share_pct"],
                "" if p["budget_eur"] is None else p["budget_eur"],
                "" if p["budget_used_ratio"] is None else p["budget_used_ratio"],
            ]
        )
    return out.getvalue()


def _eur(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f} EUR"


def render_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Cortex FinOps report {report['period']}",
        author="Cortex",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=8, leading=9.5)

    accent = colors.HexColor("#1f4e79")

    def table(data, col_widths, align_right_from=1):
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")]),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d1da")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return t

    totals = report["totals"]
    story: list = [
        Paragraph("Cortex - Cloud cost report (showback / chargeback)", h1),
        Paragraph(
            f"Period {report['period']} &nbsp;|&nbsp; generated {report['generated_at'][:16].replace('T', ' ')} UTC "
            f"&nbsp;|&nbsp; metering coverage {report['coverage_pct']}% ({report['hours_metered']} h)",
            small,
        ),
        Spacer(1, 6),
    ]

    kpis = [
        ["Fixed cloud bill (metered)", "Charged to projects", "Direct (showback)", "Platform overhead", "Idle capacity", "Not recovered"],
        [
            _eur(totals["cloud_bill_metered_eur"]),
            _eur(totals["fully_loaded_eur"]),
            _eur(totals["direct_eur"]),
            _eur(totals["platform_eur"]),
            _eur(totals["idle_eur"]),
            _eur(totals["unrecovered_eur"]),
        ],
    ]
    story.append(table(kpis, [46 * mm] * 6, align_right_from=0))
    if totals.get("projected_month_end_eur") is not None:
        story.append(Spacer(1, 3))
        story.append(
            Paragraph(f"Projected month-end charge at current allocation: <b>{_eur(totals['projected_month_end_eur'])}</b>", small)
        )

    story.append(Paragraph("Cost by department", h2))
    dept_rows = [["Department", "Projects", "Direct", "Platform", "Idle", "Total charge", "Share"]]
    for d in report["departments"]:
        dept_rows.append(
            [Paragraph(_xml_escape(d["department"]), cell), str(d["projects"]), _eur(d["direct_eur"]), _eur(d["platform_eur"]),
             _eur(d["idle_eur"]), _eur(d["fully_loaded_eur"]), f"{d['share_pct']}%"]
        )
    if len(dept_rows) == 1:
        dept_rows.append(["No metered usage in this period", "", "", "", "", "", ""])
    story.append(table(dept_rows, [60 * mm, 22 * mm, 34 * mm, 34 * mm, 34 * mm, 38 * mm, 20 * mm]))

    story.append(Paragraph("Cost by project", h2))
    proj_rows = [["Project", "Department", "Avg vCPU", "Avg RAM GB", "Avg disk GB", "Direct", "Platform", "Idle", "Total charge", "Budget used"]]
    for p in report["projects"]:
        used = "-" if p["budget_used_ratio"] is None else f"{p['budget_used_ratio'] * 100:.0f}%"
        proj_rows.append(
            [
                Paragraph(_xml_escape(p["project_name"]), cell), Paragraph(_xml_escape(p["department"]), cell),
                f"{p['avg_vcpus']:g}", f"{p['avg_ram_gb']:g}", f"{p['avg_storage_gb']:g}",
                _eur(p["direct_eur"]), _eur(p["platform_eur"]), _eur(p["idle_eur"]), _eur(p["fully_loaded_eur"]), used,
            ]
        )
    if len(proj_rows) == 1:
        proj_rows.append(["No metered usage in this period"] + [""] * 9)
    story.append(
        table(proj_rows, [42 * mm, 34 * mm, 16 * mm, 20 * mm, 20 * mm, 26 * mm, 24 * mm, 22 * mm, 28 * mm, 22 * mm], align_right_from=2)
    )

    story.append(Paragraph("How these numbers are computed", h2))
    for line in report["method"]:
        story.append(Paragraph("- " + line, small))
    if report["coverage_pct"] is not None and report["coverage_pct"] < 99.0:
        story.append(
            Paragraph(
                f"<b>Note:</b> Cortex metered only {report['coverage_pct']}% of "
                f"{'the hours elapsed so far this month' if report['is_current_period'] else 'this period' + chr(39) + 's hours'}"
                f", so totals cover the metered hours only.",
                small,
            )
        )

    doc.build(story)
    return buf.getvalue()
