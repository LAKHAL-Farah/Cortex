"""Basic causal RCA suggestion ("X caused Y").

See docs/architecture/plan-rca-causal-suggestion.md for the full design.
This is deliberately *not* a new inference engine: it's a
correlation-over-the-graph pass over two things Phase 5
(2.1-topology-graph) already builds, unmodified --

1. Currently-open `AnomalyFlag` rows (Postgres) -- the exact same query
   `routers/anomalies.py::list_anomalies` runs, keyed by `hostname`, which
   is exactly the same string as a `:Node`/`:Service`/... vertex's `id` in
   the graph (see graph_db.py's module docstring / topology_sync.py).
2. The structural `RUNS_ON`/`SERVES`/`CONNECTS` edges the graph already
   has -- read via `graph_db.fetch_vertex_detail`, the same read helper
   `routers/topology.py::get_topology_vertex` uses.

For every pair of *currently anomalous* vertices that are directly
graph-adjacent, this emits one templated "X caused Y" sentence that names
the connecting relationship -- single-hop only, no multi-hop path search,
no learned/statistical causality (see the plan doc's "Scope" section for
the full in/out-of-scope list).
"""
import logging

from sqlalchemy.orm import Session

from .. import graph_db, models

logger = logging.getLogger(__name__)

# Duplicated from anomaly_detector._SEVERITY_RANK rather than imported --
# same reasoning alert_correlation.py's own SEVERITY_RANK gives: this
# module shouldn't reach into another service module's private name for
# a three-line dict, and "higher = more severe" is a stable-enough
# contract to keep in sync by hand.
_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "normal": 0}

# Static directionality table: {(relationship, source_label, target_label):
# "source" | "target"}, keyed on the edge exactly as fetch_vertex_detail's
# own direction reports it (source -[relationship]-> target). Which side
# is "cause" is a fixed heuristic, not inferred per-pair -- see the plan
# doc's "Directionality heuristic" table. CONNECTS is deliberately absent:
# it's structural membership, not a dependency, and no metric lives on a
# Subnet/Router/FloatingIP vertex today anyway. An edge type/label pair
# missing from this table is always skipped, never guessed.
_DIRECTION = {
    ("RUNS_ON", "Service", "Node"): "target",      # Node anomaly causes Service anomaly
    ("SERVES", "Service", "Network"): "source",    # Service anomaly causes Network-adjacent anomaly
}

# One f-string per relationship type -- a raw edge name ("A RUNS_ON B")
# reads worse than a clause built for it, but the relationship name and
# both vertex ids always appear in the rendered text either way. This is
# the mechanism that satisfies the acceptance criterion ("RCA text
# references the graph relationship, not just metric names").
_TEMPLATES = {
    "RUNS_ON": (
        "{cause_id}'s {cause_metric} is {cause_severity}, which likely caused "
        "{effect_id}'s {effect_metric} anomaly, since {effect_id} RUNS_ON {cause_id}."
    ),
    "SERVES": (
        "{cause_id}'s {cause_metric} anomaly likely affected {effect_id}, "
        "since {cause_id} SERVES {effect_id}."
    ),
}


def _worst_flag(flags: list["models.AnomalyFlag"]) -> "models.AnomalyFlag":
    """The single flag that best represents a vertex's current anomaly
    state when it's anomalous on more than one metric at once -- worst
    severity first, z-score magnitude as the tiebreak."""
    return max(flags, key=lambda f: (_SEVERITY_RANK.get(f.severity, 0), abs(f.z_score)))


def _endpoint(vertex_id: str, label: str | None, flag: "models.AnomalyFlag") -> dict:
    return {
        "id": vertex_id,
        "label": label,
        "metric_name": flag.metric_name,
        "severity": flag.severity,
    }


def _render_suggestion(relationship: str, cause: dict, effect: dict) -> str:
    return _TEMPLATES[relationship].format(
        cause_id=cause["id"],
        cause_metric=cause["metric_name"],
        cause_severity=cause["severity"],
        effect_id=effect["id"],
        effect_metric=effect["metric_name"],
    )


def find_causal_suggestions(db: Session) -> list[dict]:
    """Pairs of currently-anomalous, graph-adjacent vertices, each reduced
    to one templated "X caused Y" sentence naming the connecting
    relationship. Returns a flat list, most-severe-effect first.

    Propagates whatever neo4j.exceptions.Neo4jError/ServiceUnavailable
    graph_db.fetch_vertex_detail raises -- the caller (routers/anomalies.py's
    /rca route) is responsible for turning that into a 503, the same
    pattern every other graph-backed endpoint in routers/topology.py
    follows.
    """
    rows = (
        db.query(models.AnomalyFlag)
        .filter(
            models.AnomalyFlag.severity != "normal",
            models.AnomalyFlag.manually_resolved_at.is_(None),
        )
        .all()
    )
    if not rows:
        return []

    # A host can be anomalous on more than one metric at once, so this is
    # {vertex_id: [AnomalyFlag, ...]}, not a 1:1 map.
    by_vertex: dict[str, list[models.AnomalyFlag]] = {}
    for row in rows:
        by_vertex.setdefault(row.hostname, []).append(row)

    suggestions: list[dict] = []
    # (A, B) and (B, A) are the same edge seen from both endpoints --
    # fetch_vertex_detail returns both directions already, so dedupe on
    # the unordered pair + relationship.
    seen: set[frozenset] = set()

    for vertex_id, flags in by_vertex.items():
        # One fetch_vertex_detail call per anomalous *vertex*, not per
        # metric -- cached implicitly by this being the outer loop over
        # by_vertex's keys rather than over individual flags.
        detail = graph_db.fetch_vertex_detail(vertex_id)
        if detail is None:
            # hostname has an open alert but no matching graph vertex yet
            # (e.g. topology sync hasn't caught up) -- nothing to correlate.
            continue
        vertex_label = detail["label"]

        for neighbor in detail["neighbors"]:
            neighbor_id = neighbor["id"]
            neighbor_flags = by_vertex.get(neighbor_id)
            if not neighbor_flags:
                continue  # neighbor isn't currently anomalous -- no suggestion

            relationship = neighbor["relationship"]
            pair_key = frozenset({vertex_id, neighbor_id, relationship})
            if pair_key in seen:
                continue
            seen.add(pair_key)

            # Resolve (source, target) for this edge exactly as
            # fetch_vertex_detail reported it, regardless of which side
            # vertex_id/neighbor_id happen to be in this iteration.
            if neighbor["direction"] == "outgoing":
                source_id, source_label = vertex_id, vertex_label
                target_id, target_label = neighbor_id, neighbor["label"]
            else:
                source_id, source_label = neighbor_id, neighbor["label"]
                target_id, target_label = vertex_id, vertex_label

            cause_side = _DIRECTION.get((relationship, source_label, target_label))
            if cause_side is None:
                continue  # not in the directionality table -- skip, don't guess

            if cause_side == "source":
                cause_id, cause_label = source_id, source_label
                effect_id, effect_label = target_id, target_label
            else:
                cause_id, cause_label = target_id, target_label
                effect_id, effect_label = source_id, source_label

            cause = _endpoint(cause_id, cause_label, _worst_flag(by_vertex[cause_id]))
            effect = _endpoint(effect_id, effect_label, _worst_flag(by_vertex[effect_id]))

            suggestions.append({
                "cause": cause,
                "effect": effect,
                "relationship": relationship,
                "text": _render_suggestion(relationship, cause, effect),
            })

    suggestions.sort(key=lambda s: -_SEVERITY_RANK.get(s["effect"]["severity"], 0))
    return suggestions
