import uuid
from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    metric_baselines: Mapped[list["MetricBaseline"]] = relationship(
    "MetricBaseline",
    back_populates="node",
    cascade="all, delete-orphan",
    passive_deletes=True,
)





class MetricBaseline(Base):
    __tablename__ = "metric_baselines"

    __table_args__ = (
        CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_metric_baselines_weekday",
        ),
        CheckConstraint(
            "hour BETWEEN 0 AND 23",
            name="ck_metric_baselines_hour",
        ),
        CheckConstraint(
            "sample_count >= 0",
            name="ck_metric_baselines_sample_count",
        ),
        CheckConstraint(
            "mad >= 0",
            name="ck_metric_baselines_mad",
        ),
        CheckConstraint(
            "stddev >= 0",
            name="ck_metric_baselines_stddev",
        ),
        CheckConstraint(
            "lower_bound <= upper_bound",
            name="ck_metric_baselines_bounds",
        ),
        UniqueConstraint(
            "node_id",
            "metric_name",
            "weekday",
            "hour",
            name="uq_metric_baseline_slot",
        ),
        Index(
            "ix_metric_baselines_lookup",
            "node_id",
            "metric_name",
            "weekday",
            "hour",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=False,
    )

    metric_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    weekday: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    hour: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    mean: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    stddev: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    median: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    mad: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    lower_bound: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    upper_bound: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    sample_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    node: Mapped["Node"] = relationship(
        "Node",
        back_populates="metric_baselines",
    )