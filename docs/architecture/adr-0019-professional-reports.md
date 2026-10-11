# ADR-0018: On-demand professional PDF reports (roadmap 4.11)

**Status:** Accepted
**Related code:** `services/api/app/services/reports.py` (assembly),
`services/api/app/services/report_pdf.py` (ReportLab renderers),
`services/api/app/routers/reports.py` (`GET /api/v1/reports`, `/{variant}.pdf`),
`services/web/components/FinopsReports.tsx`, `services/web/app/api/reports/[variant]/pdf/route.ts`,
tests `services/api/tests/test_reports.py`

## Context

The weekly digest (ADR-0016-weekly-digest) is an email on a schedule. Different readers
need different documents on demand: management wants posture and money at a glance,
finance wants chargeback tables, engineers want per-node capacity and CVEs.

## Decisions

1. **Three variants, one visual language.** `executive` (one page), `financial`,
   `technical`; A4 portrait, shared banner / KPI cards / tables / charts, "Page X of Y"
   footer. ReportLab only (already a dependency for the showback PDF) and built-in
   Helvetica, so there are no font files to ship. Text is sanitised to cp1252 -- OpenStack
   names can contain anything and unsupported glyphs would otherwise print as black boxes.
2. **No re-derived numbers.** Reports are assembled from the owners of each figure:
   `finops_report.build_report` (cost), `weekly_digest.collect_digest_data` (capacity,
   security, anomalies, agents; trailing 7 days) and `rightsizing_service` (VM savings).
   A figure in a PDF is the figure on the dashboard. The operational window is 7 days
   regardless of the cost period; each PDF says so.
3. **Every source is optional.** A failing source becomes an explicit "unavailable"
   note plus a line in "Data notes"; the PDF still renders. A report you cannot produce
   during an incident is worth little. The financial variant never touches Prometheus.
4. **AI is opt-in.** The executive headline is the digest's deterministic summary; `?ai=true`
   uses the LLM summary, which itself falls back to the deterministic one.
5. **Synthetic VM data is labelled** with a red "DEMO DATA" box (`?rightsizing_source=synthetic`).
6. **The UI fetches the PDF** (blob download with a spinner and the API's real error
   message) rather than a bare link, because operational variants query Prometheus and
   can take seconds. The Next route allow-lists the variant and query keys.

## Consequences

- Generated synchronously per request; fine at this scale. A cache or background job is a
  follow-up if generation time grows.
- Charts are native ReportLab graphics (vector, no image dependencies), so they are simple
  by design.
