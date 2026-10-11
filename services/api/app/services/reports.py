"""Roadmap 4.11 -- on-demand professional PDF reports (executive / financial /
technical), beyond the weekly digest email.

Nothing here re-derives numbers. Each report is assembled from the sources
that already own them, so a figure in a PDF is the figure on the dashboard:

  finance     -> finops_report.build_report   (metered showback/chargeback)
  operations  -> weekly_digest.collect_digest_data  (capacity, security,
                 anomalies, remediation, agents; trailing 7 days)
  rightsizing -> rightsizing_service.build_recommendations
  summary     -> weekly_digest.fallback_summary (deterministic) or, with
                 ``use_ai``, generate_summary (LLM, falls back to the same)

Every source is optional: a failure (Prometheus down, no VM metrics) drops
that section to an explicit "unavailable" note plus an entry in
``warnings`` -- the report still renders. A report you can't generate during
an incident is worth little.

Which sources each variant needs (so the cheap ones stay cheap -- the
financial report never touches Prometheus):

  executive  finance + operations + rightsizing + summary
  financial  finance + rightsizing
  technical  operations + rightsizing
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from . import finops_report, report_pdf, rightsizing_service, weekly_digest

logger = logging.getLogger(__name__)

VARIANTS: dict[str, dict] = {
    "executive": {
        "title": "Executive summary",
        "audience": "Management",
        "description": "Posture, headline cost and risk KPIs, decisions needed and top savings. 1-2 pages.",
        "sources": ("finance", "operations", "rightsizing"),
    },
    "financial": {
        "title": "Financial report",
        "audience": "Finance / project owners",
        "description": "Showback and chargeback by department and project, budgets, projections, right-sizing savings.",
        "sources": ("finance", "rightsizing"),
    },
    "technical": {
        "title": "Technical report",
        "audience": "Operations / engineering",
        "description": "Capacity per node, forecast breaches, security findings, anomalies, agent actions, VM sizing detail.",
        "sources": ("operations", "rightsizing"),
    },
}


def list_variants() -> list[dict]:
    return [{"id": k, "title": v["title"], "audience": v["audience"], "description": v["description"]} for k, v in VARIANTS.items()]


def _period_label(period: str) -> str:
    year, month = (int(x) for x in period.split("-"))
    return datetime(year, month, 1).strftime("%B %Y")


def collect_report_data(
    db: Session,
    variant: str,
    period: str | None = None,
    now: datetime | None = None,
    use_ai: bool = False,
    rightsizing_source: str = "auto",
) -> dict:
    """Plain-dict input for ``report_pdf``. Raises ValueError for an unknown
    variant, a malformed period or an invalid right-sizing source (the router
    maps that to 422); everything else degrades into ``warnings``."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {', '.join(VARIANTS)}")
    now = now or datetime.utcnow()
    year, month = finops_report.parse_period(period, now)  # ValueError on a bad period
    period_key = f"{year:04d}-{month:02d}"
    if rightsizing_source not in rightsizing_service.SOURCES:
        raise ValueError(f"rightsizing_source must be one of {', '.join(rightsizing_service.SOURCES)}")

    wanted = VARIANTS[variant]["sources"]
    data: dict = {
        "variant": variant,
        "period": period_key,
        "period_label": _period_label(period_key),
        "generated_at": now.isoformat() + "Z",
        "finance": None,
        "operations": None,
        "rightsizing": None,
        "summary": None,
        "warnings": [],
    }

    if "finance" in wanted:
        try:
            data["finance"] = finops_report.build_report(db, period_key, now)
        except Exception:
            logger.warning("report %s: cost data unavailable", variant, exc_info=True)
            data["warnings"].append("Cost data could not be read; the cost sections are omitted.")

    if "operations" in wanted:
        try:
            data["operations"] = weekly_digest.collect_digest_data(db, now)
            if variant == "executive":
                data["summary"] = (
                    weekly_digest.generate_summary(data["operations"]) if use_ai
                    else weekly_digest.fallback_summary(data["operations"])
                )
        except Exception:
            logger.warning("report %s: operational data unavailable", variant, exc_info=True)
            data["warnings"].append("Operational data (capacity, security, alerts) could not be collected; those sections are omitted.")

    if "rightsizing" in wanted:
        try:
            data["rightsizing"] = rightsizing_service.build_recommendations(db, source=rightsizing_source)
            if not data["rightsizing"]["available"]:
                data["warnings"].append("VM right-sizing is unavailable: " + data["rightsizing"]["reason"])
        except Exception:
            logger.warning("report %s: right-sizing unavailable", variant, exc_info=True)
            data["warnings"].append("VM right-sizing could not be computed; that section is omitted.")

    if data["operations"] is not None:
        data["warnings"].append(
            f"Operational figures cover the trailing 7 days ({data['operations']['window']['label']}), "
            "independent of the cost period."
        )
    return data


def render_report(
    db: Session,
    variant: str,
    period: str | None = None,
    now: datetime | None = None,
    use_ai: bool = False,
    rightsizing_source: str = "auto",
) -> tuple[bytes, str]:
    """(pdf_bytes, filename)."""
    data = collect_report_data(db, variant, period, now, use_ai, rightsizing_source)
    pdf = report_pdf.RENDERERS[variant](data)
    return pdf, f"cortex-{variant}-report-{data['period']}.pdf"
