import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, CheckConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

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