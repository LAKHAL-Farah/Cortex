import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, CheckConstraint, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Column, Float, UniqueConstraint, Text, JSON
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
    # Story 3.4: extra per-metric context that doesn't fit a single number --
    # currently only populated for ssh_failed_logins_5min/ssh_successful_logins_5min
    # ({"source_ips": [...]}), left null for cpu_usage/ram_usage/etc.
    details = Column(JSON, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow)
    # Suppresses a still-anomalous signal after an operator resolves it.
    manually_resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)
 
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
    # Same as AnomalyFlag.details -- see its comment.
    details = Column(JSON, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)  # NULL while still active
    resolution_type = Column(String, nullable=True)  # "automatic" | "manual"
    resolution_note = Column(Text, nullable=True)


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


class RoleBaseline(Base):
    """Same idea as Baseline, but grouped by Node.role instead of a single
    hostname (see anomaly_detector.score_current_value's second tier, story
    3.8). Lets a host with too little of its own history yet (e.g. just
    brought online) still get compared against "what's normal for a
    compute/controller/storage/monitoring node at this (weekday, hour)"
    instead of falling straight through to the context-free EWMA fallback.
    """
    __tablename__ = "role_baselines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=Monday ... 6=Sunday
    hour = Column(Integer, nullable=False)     # 0-23
    mean = Column(Float, nullable=False)
    stddev = Column(Float, nullable=False)
    median = Column(Float, nullable=False)
    mad = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    # Distinct hostnames that contributed to this slot -- same reasoning as
    # Baseline.distinct_days: enough raw points can come from just one or
    # two hosts, which isn't actually "what's normal for this role" yet.
    distinct_hosts = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("role", "metric_name", "weekday", "hour", name="uq_role_baseline_slot"),
    )


class Conversation(Base):
    """One Copilot chat thread (adr-0005's knowledge chat is stateless per
    request -- this is what turns that into something a user can leave and
    come back to). Scoped by `client_id`, an anonymous per-browser UUID the
    frontend generates and sends as X-Client-Id (see security.get_client_id)
    rather than a real account -- there's no login system in Cortex yet, so
    this is the same "shared secret" trust model the rest of the API already
    uses for X-API-Key, just one level more granular. Copying that client_id
    into another browser's storage is how a user "syncs" their history
    across devices without Cortex needing real auth.
    """
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New conversation")
    # Mirrors ChatQuery.category (adr-0005) -- which docs/knowledge/ slice
    # this thread's questions were scoped to, so resuming a conversation
    # keeps asking the same category by default.
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConversationMessage(Base):
    """One turn within a Conversation. `position` (rather than relying on
    created_at ordering) is the source of truth for transcript order --
    PUT /api/v1/conversations/{id} replaces a conversation's entire message
    list in one call (see crud.replace_conversation_messages), so ordering
    has to survive a delete-and-reinsert rather than depend on insertion
    timestamps, which can collide within the same request.
    """
    __tablename__ = "conversation_messages"

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_conversation_messages_role_allowed"),
        UniqueConstraint("conversation_id", "position", name="uq_conversation_message_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # ChatSource[] as dumped JSON (adr-0005) -- same shape the knowledge chat
    # SSE stream's `sources` event already carries. Null for user turns.
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    errored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TopologySyncRun(Base):
    """One row per completed pass of either OpenStack polling loop that
    feeds the topology graph -- topology_sync.sync_topology() (Phases 2/3)
    or prometheus_health.sync_prometheus_health() (Phase 4). Backs
    `GET /api/v1/topology/health` (Phase 5): rather than that endpoint
    trying to infer "is the sync healthy" from Neo4j state alone (which
    can't distinguish "everything's fine" from "the last pass silently
    stopped running"), each periodic pass in main.py now records its own
    outcome here, so /topology/health can answer from actual run history
    instead of guessing from a snapshot of the graph.

    Append-only by design (like AnomalyEvent) rather than one upserted row
    per sync_type -- a short history of recent runs is what makes
    "did this recover on its own, or has it been down for an hour" answerable
    from the endpoint instead of just "what's the latest status".
    """
    __tablename__ = "topology_sync_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # "openstack" (topology_sync.sync_topology, Phases 2/3) or
    # "prometheus_health" (prometheus_health.sync_prometheus_health, Phase 4).
    sync_type = Column(String, nullable=False, index=True)
    # "ok": ran and every listing/query it depends on succeeded.
    # "degraded": ran, but at least one dependency was skipped/unreachable
    #   this pass (summary still reflects whatever partial picture it got).
    # "failed": raised before producing any summary at all.
    status = Column(String, nullable=False)
    summary = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok','degraded','failed')",
            name="ck_topology_sync_runs_status_allowed",
        ),
    )
