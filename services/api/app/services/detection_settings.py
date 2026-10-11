"""How sensitive anomaly detection is, and who can change it.

Three things decide whether a reading becomes an alert, and all three are
adjustable here:

1. **z-score cut-offs** -- how many robust standard deviations from "normal"
   a CPU/RAM/login reading must be to count as medium / high / critical.
2. **SSH failed-login floor** -- the absolute failed-login counts per five
   minutes that raise an alert regardless of the learned baseline.
3. **Persistence** -- how long a reading must stay abnormal before an alert
   opens, so a one-tick blip stops paging anyone.

`balanced` is exactly what the detector has always done (2/3/4, 5/15/30, alert
on the first abnormal reading), so an installation that never touches the
setting behaves as before. `low` and `high` move all three together;
`custom` lets an admin set them one by one.

Not affected: service-state alerts (a container or node going down). Those are
facts, not statistics, so there is nothing to tune.
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)

SERVICE_STATE_METHOD = "service_state_check"
PRESET_NAMES = ("low", "balanced", "high", "custom")

# Bounds for custom values. Wide enough to be useful, tight enough that a typo
# (a z-score of 200, a persistence of a week) is rejected instead of silently
# turning detection off.
Z_MIN, Z_MAX = 0.5, 20.0
SSH_MIN, SSH_MAX = 1, 100_000
PERSIST_MAX_MINUTES = 60


@dataclass(frozen=True)
class DetectionConfig:
    preset: str
    z: dict            # {"medium": float, "high": float, "critical": float}
    ssh: dict          # {"medium": int, "high": int, "critical": int}
    persist_minutes: int

    def confirm_ticks(self, interval_seconds: float) -> int:
        """Detection ticks in a row a reading must be abnormal before an alert
        opens. 1 means "alert on the first abnormal reading"."""
        if self.persist_minutes <= 0:
            return 1
        return max(1, round(self.persist_minutes * 60 / max(1, interval_seconds)))

    def as_dict(self) -> dict:
        return {"z": dict(self.z), "ssh": dict(self.ssh), "persist_minutes": self.persist_minutes}


PRESETS: dict[str, DetectionConfig] = {
    # Fewer alerts: bigger deviations needed, and it has to last a couple of minutes.
    "low": DetectionConfig(
        "low", {"medium": 3.0, "high": 4.0, "critical": 5.0}, {"medium": 10, "high": 30, "critical": 60}, 2
    ),
    # What Cortex has always done.
    "balanced": DetectionConfig(
        "balanced", {"medium": 2.0, "high": 3.0, "critical": 4.0}, {"medium": 5, "high": 15, "critical": 30}, 0
    ),
    # More alerts, sooner.
    "high": DetectionConfig(
        "high", {"medium": 1.5, "high": 2.5, "critical": 3.5}, {"medium": 3, "high": 10, "critical": 20}, 0
    ),
}
DEFAULT_CONFIG = PRESETS["balanced"]


class InvalidSettings(ValueError):
    """The submitted numbers do not make sense; the message says which."""


def validate_custom(z: dict, ssh: dict, persist_minutes: int) -> DetectionConfig:
    try:
        zs = [float(z[k]) for k in ("medium", "high", "critical")]
        ss = [int(ssh[k]) for k in ("medium", "high", "critical")]
        persist = int(persist_minutes)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSettings("Every threshold must be a number.") from exc
    if any(math.isnan(v) or math.isinf(v) for v in zs):
        raise InvalidSettings("Thresholds must be finite numbers.")
    if not all(Z_MIN <= v <= Z_MAX for v in zs):
        raise InvalidSettings(f"Deviation thresholds must be between {Z_MIN} and {Z_MAX}.")
    if not (zs[0] < zs[1] < zs[2]):
        raise InvalidSettings("Deviation thresholds must increase: medium < high < critical.")
    if not all(SSH_MIN <= v <= SSH_MAX for v in ss):
        raise InvalidSettings(f"Failed-login thresholds must be between {SSH_MIN} and {SSH_MAX}.")
    if not (ss[0] < ss[1] < ss[2]):
        raise InvalidSettings("Failed-login thresholds must increase: medium < high < critical.")
    if not 0 <= persist <= PERSIST_MAX_MINUTES:
        raise InvalidSettings(f"The persistence time must be between 0 and {PERSIST_MAX_MINUTES} minutes.")
    return DetectionConfig(
        "custom",
        dict(zip(("medium", "high", "critical"), zs)),
        dict(zip(("medium", "high", "critical"), ss)),
        persist,
    )


def _config_from_row(row: models.DetectionSettings) -> DetectionConfig:
    if row.preset in PRESETS:
        return PRESETS[row.preset]
    return validate_custom(
        {"medium": row.z_medium, "high": row.z_high, "critical": row.z_critical},
        {"medium": row.ssh_medium, "high": row.ssh_high, "critical": row.ssh_critical},
        row.persist_minutes,
    )


def effective_config(db: Session) -> DetectionConfig:
    """The config detection should use right now. Never raises: if the table is
    missing or the stored values are unusable it logs and falls back to the
    balanced preset, because detection must keep running."""
    try:
        row = db.get(models.DetectionSettings, 1)
        return DEFAULT_CONFIG if row is None else _config_from_row(row)
    except Exception:  # noqa: BLE001
        logger.exception("could not read detection settings; using the balanced preset")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return DEFAULT_CONFIG


def get_row(db: Session) -> models.DetectionSettings:
    row = db.get(models.DetectionSettings, 1)
    if row is None:
        row = models.DetectionSettings(id=1, preset="balanced")
        db.add(row)
        db.flush()
    return row


def save(
    db: Session,
    *,
    preset: str,
    custom: Optional[dict],
    username: str,
    now: Optional[datetime] = None,
) -> DetectionConfig:
    """Persist a choice. For `custom` the numbers are validated first and kept in
    the row; for a preset the stored custom numbers are left untouched."""
    if preset not in PRESET_NAMES:
        raise InvalidSettings(f"Unknown preset '{preset}'.")
    row = get_row(db)
    if preset == "custom":
        if not custom:
            raise InvalidSettings("Custom sensitivity needs its thresholds.")
        cfg = validate_custom(custom.get("z") or {}, custom.get("ssh") or {}, custom.get("persist_minutes", 0))
        row.z_medium, row.z_high, row.z_critical = cfg.z["medium"], cfg.z["high"], cfg.z["critical"]
        row.ssh_medium, row.ssh_high, row.ssh_critical = cfg.ssh["medium"], cfg.ssh["high"], cfg.ssh["critical"]
        row.persist_minutes = cfg.persist_minutes
    row.preset = preset
    row.updated_at = now or datetime.utcnow()
    row.updated_by = username
    db.commit()
    logger.info("anomaly detection sensitivity set to %s by %s", preset, username)
    return _config_from_row(row)


# --------------------------------------------------------------------
# "What would this have done?" -- so nobody has to tune blind
# --------------------------------------------------------------------

def _severity_from_z(z: float, cut: dict) -> Optional[str]:
    z = abs(z)
    if z >= cut["critical"]:
        return "critical"
    if z >= cut["high"]:
        return "high"
    if z >= cut["medium"]:
        return "medium"
    return None


_RANK = {"medium": 1, "high": 2, "critical": 3}


def preview(
    db: Session, config: DetectionConfig, *, days: int = 7, interval_seconds: float = 60, now: Optional[datetime] = None
) -> dict:
    """Replay the last `days` of recorded alerts against `config`.

    Each stored alert keeps its peak z-score, peak value, start and end, which is
    enough to answer "would this still have fired, and how severe?". It is an
    estimate: an alert's recorded length came from the thresholds that were
    active at the time, so the persistence check is approximate. Service-state
    alerts are counted separately because no setting here touches them.
    """
    from . import auth_log_metrics  # local: keeps this module cheap to import

    now = now or datetime.utcnow()
    events = (
        db.query(models.AnomalyEvent)
        .filter(models.AnomalyEvent.started_at >= now - timedelta(days=days))
        .all()
    )
    ticks = config.confirm_ticks(interval_seconds)
    hold = (ticks - 1) * interval_seconds
    now_by, would_by = {"medium": 0, "high": 0, "critical": 0}, {"medium": 0, "high": 0, "critical": 0}
    unaffected = tuned = 0
    for e in events:
        if e.method == SERVICE_STATE_METHOD:
            unaffected += 1
            continue
        tuned += 1
        if e.severity in now_by:
            now_by[e.severity] += 1
        sev = _severity_from_z(e.z_score or 0.0, config.z)
        if e.metric_name == auth_log_metrics.FAILED_METRIC_NAME:
            count = e.current_value or 0
            absolute = "critical" if count >= config.ssh["critical"] else "high" if count >= config.ssh["high"] \
                else "medium" if count >= config.ssh["medium"] else None
            if absolute and (sev is None or _RANK[absolute] > _RANK[sev]):
                sev = absolute
        if sev is None:
            continue
        lasted = ((e.resolved_at or now) - e.started_at).total_seconds()
        if lasted < hold:
            continue
        would_by[sev] += 1
    return {
        "window_days": days,
        "alerts_raised": tuned,
        "alerts_would_raise": sum(would_by.values()),
        "by_severity_raised": now_by,
        "by_severity_would_raise": would_by,
        "unaffected_service_alerts": unaffected,
    }
