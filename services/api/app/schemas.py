import uuid
from datetime import datetime
from enum import Enum
from ipaddress import ip_address, ip_network
from typing import TypedDict
from pydantic import BaseModel, Field, field_validator, ConfigDict
from ipaddress import ip_address, ip_network

MANAGED_SUBNETS = [
    ip_network("10.0.1.0/24"),   # controller, compute1, compute2
    ip_network("10.0.2.0/24"),   # storage
]

class NodeRole(str, Enum):
    controller = "controller"
    compute = "compute"
    storage = "storage"
    monitoring = "monitoring"


class NodeBase(BaseModel):
    hostname: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    ip_address: str
    role: NodeRole
    exporter_port: int = Field(default=9100, ge=1, le=65535)
    is_active: bool = True
    node_exporter_installed: bool | None = None
    @field_validator("ip_address")
    @classmethod
    def ip_must_be_in_private_subnet(cls, v: str) -> str:
        addr = ip_address(v)
        if not any(addr in subnet for subnet in MANAGED_SUBNETS):
            allowed = ", ".join(str(s) for s in MANAGED_SUBNETS)
            raise ValueError(f"ip_address must be within one of: {allowed}")
        return v


class NodeCreate(NodeBase):
    pass


class NodeUpdate(NodeBase):
    pass





class NodeOut(NodeBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Phase 5 (API) -- topology graph read schemas.
#
# These describe the Neo4j-backed read model (see graph_db.py's
# fetch_graph/fetch_vertex_detail/fetch_services/fetch_networks), not the
# Postgres `nodes` table above. `properties`/`summary` are left as plain
# dicts rather than fully-typed models: the graph's vertex properties
# differ per label (see topology_sync.py's `_sync_*_to_graph` functions)
# and the sync summary's shape differs per sync_type (topology_sync vs.
# prometheus_health), so a fixed schema here would just duplicate what
# those modules already document and would drift the moment either one
# changes a field.
# --------------------------------------------------------------------------

class TopologyVertexOut(BaseModel):
    id: str
    label: str
    properties: dict


class TopologyEdgeOut(BaseModel):
    source: str
    target: str
    type: str


class TopologyGraphOut(BaseModel):
    nodes: list[TopologyVertexOut]
    edges: list[TopologyEdgeOut]


class TopologyNeighborOut(BaseModel):
    id: str | None
    label: str | None
    relationship: str
    direction: str


class TopologyVertexDetailOut(BaseModel):
    id: str
    label: str
    properties: dict
    neighbors: list[TopologyNeighborOut]


class TopologyServiceOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    node_id: str | None = None


class TopologyNetworkOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    subnets: list[dict] = Field(default_factory=list)
    gateway_routers: list[dict] = Field(default_factory=list)
    floating_ips: list[dict] = Field(default_factory=list)
    serving_agents: list[dict] = Field(default_factory=list)


class SyncType(str, Enum):
    openstack = "openstack"
    prometheus_health = "prometheus_health"


class SyncRunStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"
    failed = "failed"
    unknown = "unknown"  # no run recorded yet for this sync_type


class TopologySyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sync_type: str
    status: str
    summary: dict | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime


class TopologyHealthOut(BaseModel):
    """Response for GET /api/v1/topology/health. `status` is the worst of
    the two sync loops' latest-run status ('unknown' if a loop has never
    completed a pass -- e.g. right after a fresh deploy); `syncs` gives the
    latest run per sync_type so a caller can see *which* loop is degraded.
    """
    status: SyncRunStatus
    syncs: dict[str, TopologySyncRunOut | None]


# --------------------------------------------------------------------------
# Knowledge RAG (adr-0004) -- docs/knowledge/ -> Qdrant Cloud read/write schemas.
# --------------------------------------------------------------------------

class KnowledgeIngestResult(BaseModel):
    knowledge_dir: str
    collection: str
    embedding_model: str
    files_processed: int
    chunks_embedded: int
    duration_seconds: float


class KnowledgeStatus(BaseModel):
    collection: str
    exists: bool
    points_count: int | None = None
    vectors_count: int | None = None
    status: str | None = None


class KnowledgeSearchQuery(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=25)
    # One of: "service-detail" (nova/neutron/glance/keystone/cinder), "topology",
    # "network", "service-catalog", "resource-mgmt", "security-access",
    # "admin-runbook", "flow-processes", "glossary", "overview" (README.md), or
    # "general" for any unrecognized top-level file. Omit to search the whole
    # knowledge base. See loader.py::_TOP_LEVEL_CATEGORIES for the source of truth.
    category: str | None = None


class KnowledgeSearchResult(BaseModel):
    score: float
    text: str
    source_path: str
    doc_title: str
    heading: str | None = None
    category: str


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchResult]


# --------------------------------------------------------------------------
# Knowledge chat (adr-0005) -- grounded Q&A over docs/knowledge/ via NVIDIA
# NIM + LangChain, layered on top of the KnowledgeSearch* retrieval above.
# --------------------------------------------------------------------------

class ChatRole(str, Enum):
    user = "user"
    assistant = "assistant"


class ChatMessage(BaseModel):
    role: ChatRole
    content: str = Field(min_length=1)


class ChatQuery(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Prior turns, oldest first. Sent by the client on every request -- the
    # API is stateless across calls (see adr-0005), so this *is* the memory.
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    category: str | None = None
    top_k: int = Field(default=5, ge=1, le=15)


class ChatSource(BaseModel):
    source_path: str
    doc_title: str
    heading: str | None = None
    score: float


# --------------------------------------------------------------------------
# Copilot conversation history -- server-side persistence of Copilot threads,
# scoped by the anonymous X-Client-Id header (see app.security.get_client_id)
# rather than a real account, since Cortex has no login system yet. Reuses
# ChatRole/ChatSource above since a stored message is just a chat turn plus
# the bookkeeping (errored, position) needed to replay a transcript.
# --------------------------------------------------------------------------

class ConversationMessageIn(BaseModel):
    role: ChatRole
    content: str = Field(min_length=1)
    sources: list[ChatSource] | None = None
    errored: bool = False


class ConversationMessageOut(ConversationMessageIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=200)
    category: str | None = None


class ConversationUpdate(BaseModel):
    """Full-replace payload for PUT /api/v1/conversations/{id}: the client
    (see lib/copilotHistory.ts) treats a conversation as one JSON blob it
    overwrites wholesale on every turn, same as it did against localStorage
    before this endpoint existed -- so the API mirrors that shape instead of
    exposing a separate per-message append endpoint the client doesn't need.
    """
    title: str = Field(max_length=200)
    category: str | None = None
    messages: list[ConversationMessageIn] = Field(default_factory=list, max_length=500)


class ConversationSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    category: str | None
    created_at: datetime
    updated_at: datetime


class ConversationOut(ConversationSummaryOut):
    messages: list[ConversationMessageOut]


# --------------------------------------------------------------------------
# Agent orchestrator (v0.1 "prove the loop") -- POST /api/v1/agents/orchestrate
# runs the LangGraph router->monitoring->compose graph (see app/agents/) and
# returns a single JSON answer. Unlike knowledge.chat's ChatQuery/ChatSource
# above, this is intentionally not streaming yet: v0.1 is proving the graph
# mechanism runs end to end, not the UX around it.
# --------------------------------------------------------------------------

class AgentOrchestrateQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class AgentKnownNode(TypedDict):
    hostname: str
    role: str
    instance: str


class AgentOrchestrateResponse(BaseModel):
    answer: str
    agent_used: str
    raw_data: dict | None = None




