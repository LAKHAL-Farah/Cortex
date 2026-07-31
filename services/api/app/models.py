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