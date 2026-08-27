"""v0.7 (adr-0009) arbitration golden set -- tests/golden/arbitration_golden_set.json.

Drives app.services.rca_suggester.find_causal_suggestions over ~13
synthetic incidents with known ground-truth cause/effect pairs, using the
exact same fixture shapes test_rca_suggester.py already established
(_FakeDriver/_FakeResult answering graph_db.fetch_vertex_detail, a real
in-memory SQLite session for AnomalyFlag rows) -- duplicated here rather
than imported from that module, same convention every other test file in
this suite follows (each test module is self-contained; see
test_openstack_expert.py vs. test_graph_integration.py, which build their
own state/fixtures independently despite exercising overlapping code).

This is the closest real, already-implemented analog to what the v0.7
brief calls "arbitration": given several concurrent anomalies, decide
which one is the likely root cause of the others. There's no separate
`best_theory`/`critic_verdict` pair to check here the way the brief
describes for a future multi-agent design (see docs/architecture/
adr-0009's "why this, not a new concept" section) -- what's checked is
whether the engine's actual output matches the scenario's ground truth,
which is the same thing in substance.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import graph_db, models
from app.services.rca_suggester import find_causal_suggestions

_GOLDEN_SET_PATH = Path(__file__).parent / "golden" / "arbitration_golden_set.json"


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    def __init__(self, vertices: dict[str, dict]):
        self.vertices = vertices

    def run(self, query, **kwargs):
        vertex_id = kwargs["vertex_id"]
        vertex = self.vertices.get(vertex_id)
        if vertex is None:
            return _FakeResult([])
        return _FakeResult([{
            "properties": {"id": vertex_id},
            "label": vertex["label"],
            "outgoing": vertex.get("outgoing", []),
            "incoming": vertex.get("incoming", []),
        }])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, vertices: dict[str, dict]):
        self.vertices = vertices

    def session(self):
        return _FakeSession(self.vertices)


def _load_scenarios() -> list[dict]:
    with open(_GOLDEN_SET_PATH) as f:
        data = json.load(f)
    return data["scenarios"]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_flag(db, flag: dict) -> None:
    from datetime import datetime

    row = models.AnomalyFlag(
        hostname=flag["hostname"],
        metric_name=flag["metric_name"],
        current_value=flag.get("current_value", 90.0),
        z_score=flag.get("z_score", 3.5),
        severity=flag["severity"],
        method=flag.get("method", "robust_zscore"),
        baseline_n=flag.get("baseline_n", 50),
        detected_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()


@pytest.mark.parametrize("scenario", _load_scenarios(), ids=lambda s: s["id"])
def test_arbitration_golden_scenario(db, monkeypatch, scenario):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(scenario["vertices"]))
    for flag in scenario["flags"]:
        _add_flag(db, flag)

    suggestions = find_causal_suggestions(db)

    actual = {(s["cause"]["id"], s["effect"]["id"], s["relationship"]) for s in suggestions}
    expected = {
        (e["cause_id"], e["effect_id"], e["relationship"]) for e in scenario["expected_suggestions"]
    }
    assert actual == expected, (
        f"scenario {scenario['id']!r}: expected {expected}, got {actual}"
    )

    # A handful of scenarios also pin down *which* metric/severity ends up
    # representing a multi-metric cause vertex (the _worst_flag tiebreak) --
    # checked only when the golden entry specifies it.
    for expected_entry in scenario["expected_suggestions"]:
        if "cause_metric_name" not in expected_entry:
            continue
        match = next(
            s for s in suggestions
            if s["cause"]["id"] == expected_entry["cause_id"] and s["effect"]["id"] == expected_entry["effect_id"]
        )
        assert match["cause"]["metric_name"] == expected_entry["cause_metric_name"]
        assert match["cause"]["severity"] == expected_entry["cause_severity"]


def test_golden_set_has_at_least_ten_scenarios():
    # DoD: "~10-15 synthetic incidents with known ground truth" -- a
    # regression that silently shrinks the golden set to something too
    # small to mean anything should fail loudly, not just run fewer cases.
    assert len(_load_scenarios()) >= 10
