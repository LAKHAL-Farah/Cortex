"""Tests for services/quota_budget_monitor.py.

OpenStack is faked with a small object exposing `.identity.projects()`,
`.compute.get_limits(project_id=...)` and
`.block_storage.get_limits(project=...)`, whose `.absolute` is a plain
namespace with openstacksdk's *pythonic* attribute names
(`instances_used`, `total_cores`, `total_volumes_used`, ...) -- the same
attributes services/quota_budget_monitor.py::_fetch_project_limits reads
off the real `AbsoluteLimits`/`AbsoluteLimit` resource, not the raw
camelCase JSON keys those attributes are mapped from. Same faking style
as test_topology_sync.py's `_hv`/`_svc` helpers.
"""
import types

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services import quota_budget_monitor as qbm


def _db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _FakeLimits:
    def __init__(self, absolute):
        self.absolute = absolute


class _FakeComputeProxy:
    def __init__(self, absolute):
        self._absolute = absolute

    def get_limits(self, project_id=None):
        return _FakeLimits(self._absolute)


class _FakeBlockStorageProxy:
    def __init__(self, absolute):
        self._absolute = absolute

    def get_limits(self, project=None):
        return _FakeLimits(self._absolute)


class _FakeIdentityProxy:
    def __init__(self, projects):
        self._projects = projects

    def projects(self):
        return self._projects


def _project(id_, name):
    return types.SimpleNamespace(id=id_, name=name)


def _fake_conn(compute_absolute, block_storage_absolute, projects):
    return types.SimpleNamespace(
        identity=_FakeIdentityProxy(projects),
        compute=_FakeComputeProxy(compute_absolute),
        block_storage=_FakeBlockStorageProxy(block_storage_absolute),
    )


def _compute_absolute(**overrides):
    defaults = dict(
        instances_used=2, instances=20,
        total_cores_used=4, total_cores=24,
        total_ram_used=8192, total_ram=51200,
        floating_ips_used=1, floating_ips=6,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _volume_absolute(**overrides):
    defaults = dict(
        total_volumes_used=2, max_total_volumes=12,
        total_gigabytes_used=40, max_total_volume_gigabytes=500,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


NORMAL_COMPUTE = _compute_absolute()
NORMAL_VOLUME = _volume_absolute()


def test_capacity_cap_breach_message_names_capacity_cap_not_budget():
    """A project pinned against its instance quota (used == limit) must
    raise a capacity_cap alert whose message explicitly says "CAPACITY
    CAP", not a generic threshold sentence and not budget wording."""
    db = _db()
    compute = _compute_absolute(instances_used=20, instances=20)
    conn = _fake_conn(compute, NORMAL_VOLUME, [_project("proj-1", "stagiaires-ete-2026")])

    qbm.check_quota_and_budget(db, conn=conn)

    row = (
        db.query(models.QuotaAlert)
        .filter_by(project_id="proj-1", breach_type="capacity_cap", resource="instances")
        .one()
    )
    assert row.severity == "critical"
    assert "CAPACITY CAP" in row.message
    assert "BUDGET" not in row.message
    assert row.ratio == 1.0


def test_budget_cap_breach_message_names_budget_cap_not_capacity(monkeypatch):
    """A project with plenty of quota headroom but configured with a low
    budget must raise a budget_cap alert -- distinct wording, and no
    capacity_cap alert should fire alongside it since quota usage is low."""
    monkeypatch.setattr(
        qbm, "_load_project_budgets", lambda: {"proj-2": 1.0}
    )
    db = _db()
    # Low quota usage (well under warning ratio) but the estimated cost
    # from that usage still exceeds the tiny configured budget.
    compute = _compute_absolute(total_cores_used=4, total_ram_used=8192)
    conn = _fake_conn(compute, NORMAL_VOLUME, [_project("proj-2", "mern-prod")])

    qbm.check_quota_and_budget(db, conn=conn)

    capacity_rows = (
        db.query(models.QuotaAlert)
        .filter_by(project_id="proj-2", breach_type="capacity_cap")
        .all()
    )
    assert all(r.severity == "normal" for r in capacity_rows)

    budget_row = (
        db.query(models.QuotaAlert)
        .filter_by(project_id="proj-2", breach_type="budget_cap", resource="estimated_cost_eur")
        .one()
    )
    assert budget_row.severity == "critical"
    assert "BUDGET CAP" in budget_row.message
    assert "CAPACITY" not in budget_row.message


def test_unlimited_quota_is_never_flagged_as_capacity_breach():
    """OpenStack reports -1 for an unlimited quota -- that must never be
    treated as "0% headroom" or otherwise flagged."""
    db = _db()
    compute = _compute_absolute(instances=-1, instances_used=500)
    conn = _fake_conn(compute, NORMAL_VOLUME, [_project("admin", "admin")])

    qbm.check_quota_and_budget(db, conn=conn)

    row = (
        db.query(models.QuotaAlert)
        .filter_by(project_id="admin", breach_type="capacity_cap", resource="instances")
        .first()
    )
    assert row is None


def test_project_without_configured_budget_gets_no_budget_alert(monkeypatch):
    monkeypatch.setattr(qbm, "_load_project_budgets", lambda: {})
    db = _db()
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [_project("proj-3", "no-budget-project")])

    qbm.check_quota_and_budget(db, conn=conn)

    rows = db.query(models.QuotaAlert).filter_by(project_id="proj-3", breach_type="budget_cap").all()
    assert rows == []


def test_rerun_upserts_same_slot_instead_of_duplicating():
    db = _db()
    compute = _compute_absolute(instances_used=20, instances=20)
    conn = _fake_conn(compute, NORMAL_VOLUME, [_project("proj-1", "stagiaires-ete-2026")])

    qbm.check_quota_and_budget(db, conn=conn)
    qbm.check_quota_and_budget(db, conn=conn)

    rows = (
        db.query(models.QuotaAlert)
        .filter_by(project_id="proj-1", breach_type="capacity_cap", resource="instances")
        .all()
    )
    assert len(rows) == 1


def test_resolved_breach_reverts_to_normal_severity():
    """A slot that clears on a later pass must flip back to "normal",
    not stay stuck at its last breached severity (same convention as
    AnomalyFlag)."""
    db = _db()
    breached_compute = _compute_absolute(instances_used=20, instances=20)
    conn = _fake_conn(breached_compute, NORMAL_VOLUME, [_project("proj-1", "stagiaires-ete-2026")])
    qbm.check_quota_and_budget(db, conn=conn)

    healed_compute = _compute_absolute(instances_used=2, instances=20)
    conn2 = _fake_conn(healed_compute, NORMAL_VOLUME, [_project("proj-1", "stagiaires-ete-2026")])
    qbm.check_quota_and_budget(db, conn=conn2)

    row = (
        db.query(models.QuotaAlert)
        .filter_by(project_id="proj-1", breach_type="capacity_cap", resource="instances")
        .one()
    )
    assert row.severity == "normal"
    assert row.message is None
