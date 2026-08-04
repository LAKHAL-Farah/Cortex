import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, CheckConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Column,  Float, UniqueConstraint
from .db import Base


class Node(Base):
    __tablename__ = "nodes"

    __table_args__ = (
        CheckConstraint(
            "role IN ('controller','compute','storage','monitoring')",
            name="ck_nodes_role_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    hostname: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )

    ip_address: Mapped[str] = mapped_column(
        String(15),
        unique=True,
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    exporter_port: Mapped[int] = mapped_column(
        Integer,
        default=9100,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    node_exporter_installed: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )




class AnomalyFlag(Base):
    __tablename__ = "anomaly_flags"
 
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False)
    current_value = Column(Float, nullable=False)
    z_score = Column(Float, nullable=False)
    severity = Column(String, nullable=False)  # "medium" | "high" | "critical" | "normal"
    method = Column(String, nullable=False, default="robust_zscore")  # "robust_zscore" | "ewma_fallback"
    baseline_n = Column(Integer, nullable=True)  # sample count backing the baseline used (None if EWMA fallback)
    detected_at = Column(DateTime, default=datetime.utcnow)
 
    __table_args__ = (
        UniqueConstraint("hostname", "metric_name", name="uq_anomaly_slot"),
    )
 
 
class AnomalyEvent(Base):
    """History of anomaly episodes, independent from AnomalyFlag.

    AnomalyFlag is a single upserted row per (hostname, metric_name) that
    only reflects the *current* state -- it gets overwritten on every
    detection tick, so nothing about a past anomaly survives once it
    resolves or a new one starts. This table is append-only: one row per
    episode, opened when a host/metric first crosses into an anomalous
    severity and closed (resolved_at set) once it drops back to "normal",
    so the Alerts > History page has something to actually show.
    """
    __tablename__ = "anomaly_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False)
    # Peak values reached while the episode was open (severity only ever
    # ratchets up within an episode; a fresh episode starts if it dips back
    # to normal and re-triggers later).
    current_value = Column(Float, nullable=False)
    z_score = Column(Float, nullable=False)
    severity = Column(String, nullable=False)
    method = Column(String, nullable=False, default="robust_zscore")
    baseline_n = Column(Integer, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)  # NULL while still active


class EwmaState(Base):
    """Persisted online mean/variance estimate, used as a fallback when a
    (weekday, hour) baseline slot doesn't exist yet or is too thin to trust
    (see MIN_BASELINE_SAMPLES in anomaly_detector.py). Restart-safe: state
    survives API restarts instead of resetting to zero.
    """
    __tablename__ = "ewma_state"
 
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False)
    mean = Column(Float, nullable=False)
    var = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow)
 
    __table_args__ = (
        UniqueConstraint("hostname", "metric_name", name="uq_ewma_slot"),
    )


class Baseline(Base):
    __tablename__ = "baselines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=Monday ... 6=Sunday
    hour = Column(Integer, nullable=False)     # 0-23
    mean = Column(Float, nullable=False)
    stddev = Column(Float, nullable=False)
    median = Column(Float, nullable=False)
    mad = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    # Distinct calendar days that contributed a sample to this slot, as
    # opposed to sample_count's raw point count. A single hour's worth of
    # 5-minute-step points (up to 12) all come from *one* real occurrence of
    # this (weekday, hour) and are highly autocorrelated -- they can clear
    # MIN_BASELINE_SAMPLES without the slot having ever actually seen a
    # second day, which is what let brand-new/thin history get trusted as a
    # real baseline (see anomaly_detector.MIN_BASELINE_DAYS).
    distinct_days = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("hostname", "metric_name", "weekday", "hour", name="uq_baseline_slot"),
    )