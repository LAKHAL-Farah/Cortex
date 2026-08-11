"""Phase 6 of the topology-graph feature: correlate open anomaly alerts
into incidents using the graph Phases 2-5 already build, instead of
leaving each `AnomalyFlag` row as an unrelated line in the Alerts list.

Input is two things that already exist -- no new detection logic and no
new graph schema (see the action plan doc, section 6 "explicitly out of
scope"):

1. The currently-open rows from `AnomalyFlag` (Postgres) -- the exact
   same query `routers/anomalies.py::list_anomalies` already runs.
2. A read-only pass over the topology graph via `graph_db.py`'s shared
   driver, over the same three relationship types every other Phase-5/6
   read uses: `RUNS_ON`, `SERVES`, `CONNECTS`.

`AnomalyFlag.hostname` is treated as "the id of whatever graph vertex
this alert is anchored to" rather than literally always a `:Node`
hostname. In practice it almost always *is* a Node hostname, since
anomaly_detector.py only ever scores node_exporter metrics (cpu_usage,
ram_usage) today and a Node vertex's `id` is exactly that hostname (see
prometheus_health.py's module docstring) -- no lookup table needed, the
mapping is the identity function. But nothing about `AnomalyFlag` ties
`hostname` to `:Node` specifically, and vertex ids are unique across the
whole graph regardless of label (topology-api-endpoints.md), so a plain
`MATCH (v) WHERE v.id = $hostname` resolves an alert to its vertex
whatever label that vertex turns out to have -- which is what lets a
future (or synthetic/test) alert anchor directly at a `:Service` or
`:Network` vertex without this module caring.

Two open alerts are correlated when their anchor vertices are the same
vertex, or are connected by a path of length <=2 over
RUNS_ON/SERVES/CONNECTS edges, direction ignored (see the action plan
doc, section 2, for why direction doesn't matter here -- "this Service
RUNS_ON this Node" is the same structural fact read either way). Alerts
whose hostname doesn't resolve to any vertex (e.g. topology sync hasn't
run yet) or whose vertex has no such path to any other open alert's
vertex stay ungrouped, same as today.

Incidents are computed fresh on every call, not persisted -- see section
6's "no write path into Neo4j".
"""
import hashlib
import logging
from collections import defaultdict
from datetime import timezone

from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.orm import Session

from .. import graph_db, models

logger = logging.getLogger(__name__)

# Two alerts are correlated if their anchor vertices are within this many
# hops of each other over RUNS_ON/SERVES/CONNECTS -- see the action plan
# doc, section 2: "Everything past that... stays as two separate alerts".
MAX_HOPS = 2

SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "normal": 0}

# Short label used in the "under {X} pressure" narrative clause -- kept
# distinct from lib/anomalies.ts's METRIC_LABEL (which says "CPU usage")
# since "under CPU usage pressure" reads worse than "under CPU pressure".
_METRIC_SHORT_LABEL = {"cpu_usage": "CPU", "ram_usage": "memory"}

# Narrative clauses put a :Node's own metric anomaly first (it reads as
# the sentence's subject -- "compute-02 is under CPU pressure and
# its ... service has gone unreachable" -- not the other way around),
# then :Service, then everything else. Unresolved anchors (no vertex
# found in the graph) are treated like :Node so a standalone alert still
# reads the same as it does today.
_LABEL_NARRATIVE_RANK = {None: 0, "Node": 0, "Service": 1}


def _iso_utc(dt) -> str | None:
    """Same fix as routers/anomalies.py::_iso_utc (naive-UTC datetimes
    need an explicit tzinfo before .isoformat(), or a browser's `new
    Date(...)` silently reads them as local time) -- duplicated rather
    than imported so this module doesn't depend on the router module
    (routers/anomalies.py imports *this* module, not the other way
    around).
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _open_alert_rows(db: Session) -> list[models.AnomalyFlag]:
    """The exact same "currently open" set routers/anomalies.py::list_anomalies
    returns, kept as its own query here too so build_incidents() doesn't
    have to be called through the router (e.g. from tests)."""
    return (
        db.query(models.AnomalyFlag)
        .filter(models.AnomalyFlag.severity != "normal")
        .all()
    )


def _alert_to_dict(row: models.AnomalyFlag) -> dict:
    return {
        "hostname": row.hostname,
        "metric_name": row.metric_name,
        "current_value": row.current_value,
        "z_score": row.z_score,
        "severity": row.severity,
        "method": row.method,
        "baseline_n": row.baseline_n,
        "detected_at": _iso_utc(row.detected_at),
    }


class _UnionFind:
    """Textbook union-find over the distinct anchor ids -- see the action
    plan doc, section 3.1: "Union-find (or a simple graph
    connected-components pass over just the open-alert vertices) groups
    them into incidents." No union-by-rank/path-compression-by-rank
    needed at this scale (bounded by the number of *distinct hosts*
    with an open alert, never large)."""

    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _resolve_vertices(session, ids: list[str]) -> dict[str, dict]:
    """{anchor_id: {"label": ..., "properties": {...}}} for every id that
    actually matches a vertex in the graph -- ids with no match are
    simply absent, same convention as graph_db.fetch_vertex_detail
    returning None for an unknown vertex_id."""
    result = session.run(
        "MATCH (v) WHERE v.id IN $ids "
        "RETURN v.id AS id, labels(v)[0] AS label, properties(v) AS properties",
        ids=ids,
    )
    return {r["id"]: {"label": r["label"], "properties": r["properties"]} for r in result}


def _reachable_within(session, anchor_id: str, candidate_ids: list[str]) -> set[str]:
    """Which of `candidate_ids` (other open alerts' anchor ids) are within
    MAX_HOPS of `anchor_id`, via RUNS_ON/SERVES/CONNECTS, direction
    ignored. Restricting to `candidate_ids` (rather than "everything
    reachable") keeps this to exactly the question build_incidents()
    needs answered and keeps the result set small regardless of how big
    the underlying graph is.
    """
    result = session.run(
        """
        MATCH (start)-[:RUNS_ON|SERVES|CONNECTS*1..2]-(other)
        WHERE start.id = $anchor_id AND other.id IN $candidate_ids AND other.id <> $anchor_id
        RETURN DISTINCT other.id AS id
        """,
        anchor_id=anchor_id,
        candidate_ids=candidate_ids,
    )
    return {r["id"] for r in result}


def _fetch_shortest_path(session, id1: str, id2: str) -> tuple[list[dict], list[dict]]:
    """The actual shortest RUNS_ON/SERVES/CONNECTS path (<=MAX_HOPS)
    between two anchor vertices already known to be within reach of each
    other -- used for `graph_path` (what the "View on graph" link
    highlights) and for the narrative's vertex lookups. Returns
    ([{"id","label"}, ...], [{"type","source","target"}, ...]); empty
    lists if, unexpectedly, no such path exists (e.g. a race with a
    sync pass removing the edge between the reachability check above and
    this call) rather than raising.
    """
    result = session.run(
        f"""
        MATCH p = shortestPath((a)-[:RUNS_ON|SERVES|CONNECTS*1..{MAX_HOPS}]-(b))
        WHERE a.id = $id1 AND b.id = $id2
        RETURN [n IN nodes(p) | {{id: n.id, label: labels(n)[0]}}] AS path_nodes,
               [r IN relationships(p) | {{type: type(r), source: startNode(r).id, target: endNode(r).id}}] AS path_edges
        """,
        id1=id1,
        id2=id2,
    )
    record = result.single()
    if record is None:
        return [], []
    return record["path_nodes"], record["path_edges"]


def _incident_id(members: list[dict]) -> str:
    """Deterministic id derived from the member set, not a random uuid --
    stays the same across repeated polls of the same open-alert set (the
    web app's 10s SWR refresh) so the UI's expand/collapse state doesn't
    reset every refresh, without persisting anything to Postgres/Neo4j.
    """
    key = "|".join(sorted(f"{m['hostname']}::{m['metric_name']}" for m in members))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _pick_root_cause(anchor_ids: list[str], members: list[dict]) -> str | None:
    """Best-guess root cause: the anchor whose worst member alert is most
    severe, tie-broken by whichever anchor's alert fired first -- "started
    the chain" is as good a tiebreak as this data supports without a real
    causal model, and it's deterministic and explainable, per the action
    plan's `root_cause_guess` being a "guess", not a certainty.
    """
    best_id, best_key = None, None
    for anchor_id in anchor_ids:
        anchor_members = [m for m in members if m["hostname"] == anchor_id]
        if not anchor_members:
            continue
        rank = max(SEVERITY_RANK.get(m["severity"], 0) for m in anchor_members)
        earliest = min((m["detected_at"] or "") for m in anchor_members)
        key = (-rank, earliest)
        if best_key is None or key < best_key:
            best_key, best_id = key, anchor_id
    return best_id


def _prettify_service_name(binary: str | None, fallback: str) -> str:
    if not binary:
        return fallback
    return binary.replace("-", " ").replace("_", " ").capitalize()


def _metric_short_label(metric_name: str) -> str:
    return _METRIC_SHORT_LABEL.get(metric_name, metric_name.replace("_", " "))


def _member_clause(anchor_id: str, anchor_members: list[dict], vertex_info: dict) -> str:
    """One clause describing what's wrong at this anchor, templated off
    the vertex's own label/properties (action plan section 3.1:
    "`narrative` is templated off the vertex labels/edge types in the
    path... not free-text generation -- keeps it deterministic and
    testable"). Mirrors buildInsight()'s spirit in lib/anomalies.ts (a
    sentence built from the record's own fields), just server-side and
    over a vertex instead of a single metric.
    """
    vertex = vertex_info.get(anchor_id)
    label = vertex["label"] if vertex else None

    if label == "Service":
        props = vertex.get("properties") or {}
        service_name = _prettify_service_name(props.get("binary"), anchor_id)
        state = props.get("state") or props.get("openstack_state") or "flagged"
        return f"its {service_name} service has gone {state}"

    if label in ("Network", "Router", "Subnet", "FloatingIP"):
        props = vertex.get("properties") or {}
        name = props.get("name") or props.get("floating_ip_address") or anchor_id
        status = (props.get("status") or "degraded").lower()
        return f"the {name} {label.lower()} is {status}"

    # :Node, or an anchor with no matching vertex at all (topology sync
    # hasn't run yet) -- describe it by metric, the same way a standalone
    # alert already reads today.
    shorts = [_metric_short_label(m["metric_name"]) for m in anchor_members]
    if len(shorts) == 1:
        return f"{anchor_id} is under {shorts[0]} pressure"
    return f"{anchor_id} is under {', '.join(shorts[:-1])} and {shorts[-1]} pressure"


def _build_narrative(members: list[dict], vertex_info: dict) -> str:
    by_anchor: dict[str, list[dict]] = defaultdict(list)
    for m in members:
        by_anchor[m["hostname"]].append(m)

    def sort_key(anchor_id: str):
        vertex = vertex_info.get(anchor_id)
        label = vertex["label"] if vertex else None
        rank = _LABEL_NARRATIVE_RANK.get(label, 2)
        earliest = min((m["detected_at"] or "") for m in by_anchor[anchor_id])
        return (rank, earliest)

    ordered_anchors = sorted(by_anchor, key=sort_key)
    clauses = [_member_clause(aid, by_anchor[aid], vertex_info) for aid in ordered_anchors]

    if len(clauses) == 1:
        sentence = clauses[0]
    elif len(clauses) == 2:
        sentence = f"{clauses[0]} and {clauses[1]}"
    else:
        sentence = f"{', '.join(clauses[:-1])}, and {clauses[-1]}"
    return f"{sentence}."


def _build_graph_path(session, anchor_ids: list[str], root: str | None, vertex_info: dict) -> dict | None:
    """`{"vertex_ids": [...], "edges": [...]}` covering the root cause's
    vertex plus a shortest path out to every other distinct anchor in the
    incident -- enough for the web app's "View on graph" link to
    highlight the connected region on /topology. None if the root itself
    never resolved to a vertex (nothing to highlight)."""
    if root is None or root not in vertex_info:
        return None

    vertices: dict[str, dict] = {root: {"id": root, "label": vertex_info[root]["label"]}}
    edges: list[dict] = []
    for other in anchor_ids:
        if other == root or other not in vertex_info:
            continue
        path_nodes, path_edges = _fetch_shortest_path(session, root, other)
        for n in path_nodes:
            vertices[n["id"]] = n
        edges.extend(path_edges)

    return {"vertex_ids": sorted(vertices), "edges": edges}


def _finalize_incident(anchor_ids: list[str], members: list[dict], vertex_info: dict, graph_path: dict | None) -> dict:
    root = _pick_root_cause(anchor_ids, members)
    root_vertex = vertex_info.get(root) if root else None
    return {
        "incident_id": _incident_id(members),
        "severity": members[0]["severity"],  # members are pre-sorted worst-first
        "member_count": len(members),
        "root_cause_guess": {"vertex_id": root, "label": root_vertex["label"] if root_vertex else None} if root else None,
        "narrative": _build_narrative(members, vertex_info),
        "members": members,
        "graph_path": graph_path,
    }


def build_incidents(db: Session) -> list[dict]:
    """Every currently-open AnomalyFlag, grouped into incidents via the
    topology graph. Always returns a flat list (mirroring
    /api/v1/anomalies's own bare-list shape) -- an alert with no
    correlated peer comes back as its own incident with `member_count:
    1`, so the web app can group by `incident_id` uniformly instead of
    special-casing "no incident" (see AlertsView.tsx).

    If the graph is unreachable, degrades to one incident per alert
    (today's behavior) instead of a hard failure -- Postgres alerting
    must keep working even when the graph sync loop or Neo4j itself is
    down (see the action plan doc, section 3.2).
    """
    rows = _open_alert_rows(db)
    if not rows:
        return []

    alerts = [_alert_to_dict(r) for r in rows]
    anchor_ids = sorted({a["hostname"] for a in alerts})
    anchor_to_alerts: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        anchor_to_alerts[a["hostname"]].append(a)

    try:
        with graph_db.driver.session() as session:
            vertex_info = _resolve_vertices(session, anchor_ids)

            uf = _UnionFind(anchor_ids)
            for anchor_id in anchor_ids:
                if anchor_id not in vertex_info:
                    continue
                for other_id in _reachable_within(session, anchor_id, anchor_ids):
                    uf.union(anchor_id, other_id)

            grouped: dict[str, list[str]] = defaultdict(list)
            for anchor_id in anchor_ids:
                grouped[uf.find(anchor_id)].append(anchor_id)

            incidents = []
            for group_anchor_ids in grouped.values():
                members = sorted(
                    (m for aid in group_anchor_ids for m in anchor_to_alerts[aid]),
                    key=lambda a: (-SEVERITY_RANK.get(a["severity"], 0), a["detected_at"] or ""),
                )
                root = _pick_root_cause(group_anchor_ids, members)
                graph_path = _build_graph_path(session, group_anchor_ids, root, vertex_info)
                incidents.append(_finalize_incident(group_anchor_ids, members, vertex_info, graph_path))
    except (Neo4jError, ServiceUnavailable):
        logger.exception(
            "alert correlation: topology graph unreachable, falling back to ungrouped alerts"
        )
        incidents = [
            _finalize_incident([anchor_id], anchor_to_alerts[anchor_id], {}, None)
            for anchor_id in anchor_ids
        ]

    incidents.sort(key=lambda i: (-SEVERITY_RANK.get(i["severity"], 0), -i["member_count"]))
    return incidents
