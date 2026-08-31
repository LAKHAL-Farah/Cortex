from datetime import datetime

from app import models
from app.services.alert_email import _event_body


def test_alert_email_includes_actionable_event_context():
    event = models.AnomalyEvent(
        hostname="compute-01",
        metric_name="cpu_usage",
        severity="critical",
        current_value=97.5,
        z_score=4.2,
        method="robust_zscore",
        baseline_n=42,
        details={"source_ips": ["192.0.2.9"]},
        started_at=datetime(2026, 8, 31, 12, 0, 0),
    )

    body = _event_body(event)

    assert "Host/service: compute-01" in body
    assert "Current value: 97.5" in body
    assert "Anomaly score (z-score): 4.20" in body
    assert "Baseline samples: 42" in body
    assert "source_ips" in body
