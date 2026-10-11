# ADR-0016 — Weekly digest email (roadmap 4.5)

**Status:** accepted · **Acceptance:** digest auto-sent on schedule, real data from the week.

## Decision

A scheduled HTML email summarising the last 7 days (vs. the 7 before): capacity
trend, security posture, and what the agents caught, with an AI-written
summary on top.

- **Data** (`services/weekly_digest.py`): Prometheus range queries (same PromQL
  as the forecast dataset), the forecast threshold scan, `quota_alerts`,
  `security_finding_cache` + `security_scan_runs`, `anomaly_events`,
  `agent_traces`, and the remediation audit log. Each external source degrades
  its own section (`available: false`) instead of failing the digest.
- **AI summary:** one `reasoning`-tier NVIDIA NIM call that receives only the
  aggregated numbers and must reply with JSON. Missing key, timeout (60 s),
  or unparseable output falls back to a deterministic summary from the same
  data, so delivery never depends on the LLM (cf. the NIM incident notes in
  `llm_client.py`).
- **Rendering** (`services/weekly_digest_email.py`): table layout + inline
  styles, no images, app palette/fonts, mobile stacking, dark mode, plain-text
  alternative. Counts, hostnames and CVE ids only — no IPs or log contents.
- **Scheduling:** a 5-minute tick in the existing `_run_periodic` idiom. State
  lives in `alert_email_settings.digest_last_sent_at` (restart-safe); the row
  is locked and the slot claimed *before* sending (multi-replica safe), and
  restored on failure (retry next tick). First run seeds instead of sending;
  stale slots (> `DIGEST_CATCHUP_HOURS`) are skipped.
- **API/UI:** `GET/PUT /settings/weekly-digest` (PUT admin-only),
  `POST .../send` (admin-only, doesn't move the schedule), `GET .../preview`.
  Settings page gets a "Weekly digest" panel with schedule, send-now, preview.

## Consequences

- Reuses the single shared recipient and SMTP config of alert emails.
- No cross-week security trend: `security_finding_cache` holds only the current
  state. A history table would be needed to show "new vs. last week".
- Day/hour are UTC.
