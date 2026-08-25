import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime, CheckConstraint, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Column, Float, UniqueConstraint, Text, JSON
from .db import Base


class User(Base):
    """A real Cortex account (see app/auth.py) -- username + bcrypt password
    hash, a coarse role, and the bits needed to run an admin-invites-users
    flow instead of open self-signup:

    - `is_active`: soft-disable switch. Admins flip this off instead of
      deleting the row, so a deactivated user's audit trail/ownership of
      past data doesn't disappear, and re-enabling doesn't need a new id.
    - `must_change_password`: set whenever an admin sets/resets someone's
      password (including the bootstrap admin account, see main.py's
      startup seeding) so a shared/temporary password can't silently
      become a long-lived credential -- the frontend forces a password
      change before letting that session do anything else.

    role is a plain string CHECK rather than a permissions table -- Cortex
    only needs two tiers right now (operators who can act on the platform,
    and admins who can additionally manage nodes/knowledge-base ingestion
    and other accounts). If that grows past "admin can do everything viewer
    can, plus X", revisit with real per-permission rows instead of adding
    more roles here.
    """
    __tablename__ = "users"

    __table_args__ = (
        CheckConstraint("role IN ('admin','viewer')", name="ck_users_role_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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


class QuotaAlert(Base):
    """Quota/budget breach alerts -- distinct from `AnomalyFlag`.

    `AnomalyFlag` answers "is this host's *measured resource usage*
    (cpu_usage/ram_usage from node_exporter) behaving abnormally versus
    its own history". This table answers a completely different
    question: "is this OpenStack *project* running up against a hard
    ceiling" -- and there are two unrelated ceilings a project can hit:

    - `capacity_cap`: an OpenStack quota (Nova/Cinder `GET /limits`) --
      e.g. a project physically cannot boot another VM because it's at
      its `maxTotalInstances`. This is an infrastructure limit; raising
      it costs nothing by itself, an admin just has to run
      `openstack quota set`.
    - `budget_cap`: an estimated-cost ceiling configured per project
      (services/quota_budget_monitor.py's `PROJECT_BUDGETS_EUR`) --
      e.g. the stagiaires-ete-2026 project's *estimated* monthly spend
      has crossed the amount RIF SAS is willing to allocate it. This is
      a spending limit; the project may still have plenty of quota
      headroom left when this fires.

    A project silently allocating right up to a hard quota and a project
    quietly costing more than intended are different problems needing
    different responses, so `breach_type` and the row's `message` always
    say which one this is -- never a bare "threshold exceeded".

    One row per (project_id, breach_type, resource) "slot", upserted on
    every check_quota_and_budget() pass -- same convention as
    AnomalyFlag: the row is kept (with severity="normal") even once a
    breach clears, rather than deleted, so a slot's last-known state is
    always a single lookup away instead of "absent = fine, but was it
    ever checked at all?".
    """
    __tablename__ = "quota_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String, nullable=False, index=True)
    project_name = Column(String, nullable=False)
    # "capacity_cap" (OpenStack quota, e.g. Nova/Cinder limits) or
    # "budget_cap" (configured estimated-cost ceiling).
    breach_type = Column(String, nullable=False)
    # For capacity_cap: "instances" | "vcpus" | "ram_mb" | "floating_ips" |
    # "volumes" | "gigabytes". For budget_cap: always "estimated_cost_eur".
    resource = Column(String, nullable=False)
    used = Column(Float, nullable=False)
    limit = Column(Float, nullable=False)
    ratio = Column(Float, nullable=False)  # used / limit
    severity = Column(String, nullable=False)  # "normal" | "warning" | "critical"
    # Human-readable sentence that always names which cap this is --
    # "capacity cap" or "budget cap" -- never a generic "limit reached".
    # Precomputed here (rather than only in the router) so it survives a
    # direct DB read/export unchanged.
    message = Column(Text, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "breach_type IN ('capacity_cap','budget_cap')",
            name="ck_quota_alerts_breach_type_allowed",
        ),
        CheckConstraint(
            "severity IN ('normal','warning','critical')",
            name="ck_quota_alerts_severity_allowed",
        ),
        UniqueConstraint(
            "project_id", "breach_type", "resource", name="uq_quota_alert_slot"
        ),
    )


class Conversation(Base):
    """One Copilot chat thread (adr-0005's knowledge chat is stateless per
    request -- this is what turns that into something a user can leave and
    come back to). Scoped by `user_id`, the logged-in account (app/auth.py)
    that started the thread -- history now follows the account rather than
    a per-browser id, so it shows up the same way on any device that account
    logs into. (This replaced an earlier client_id/X-Client-Id scheme from
    before Cortex had real accounts; see migration b1c2d3e4f5a6's successor
    for the cutover, which drops any conversations that predate it since an
    anonymous browser id can't be attributed to a user after the fact.)
    """
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    # Which specialist agent produced this turn (monitoring/prediction/rag,
    # see app/agents/) and the raw payload it returned (LiveMetrics, a
    # forecast series, or RAG sources -- see AgentOrchestrateResponse). Both
    # null for user turns and for any assistant turn that predates the
    # agent orchestrator UI. The frontend uses agent_used to pick which
    # panel to render a saved turn with on reload (components/
    # CopilotAgentPanels.tsx), so this is persisted alongside content
    # instead of being re-derived, which the orchestrator has no way to do
    # after the fact.
    agent_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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