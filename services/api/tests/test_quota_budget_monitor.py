"""Tests for services/quota_budget_monitor.py.

OpenStack is faked with a small object exposing `.identity.projects()`,
`.compute.get_limits(tenant_id=...)` (whose `.absolute` is a plain
namespace with openstacksdk's *pythonic* attribute names --
`instances_used`, `total_cores`, ... -- the same attributes
services/quota_budget_monitor.py::_fetch_project_limits reads off the real
`AbsoluteLimits` resource) and `.block_storage.get_quota_set(project,
usage=True)`. Volume numbers are given to the fake in the old
`_volume_absolute` shape for brevity and converted to the SDK's
quota-set shape (`volumes` = limit, `usage["volumes"]` = in_use). Same faking style
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
    def __init__(self, absolute, hypervisors):
        self._absolute = absolute
        self._hypervisors = hypervisors

    def get_limits(self, tenant_id=None, **unexpected):
        # Nova's selector is `tenant_id`; anything else (e.g. `project_id`)
        # would make real Nova answer for the *token's own* project.
        assert not unexpected, f"unexpected get_limits kwargs: {unexpected}"
        return _FakeLimits(self._absolute)

    def hypervisors(self, details=False):
        if self._hypervisors is None:
            raise RuntimeError("hypervisor stats need an admin credential")
        return self._hypervisors


class _FakeBlockStorageProxy:
    def __init__(self, absolute, pools):
        self._absolute = absolute
        self._pools = pools

    def get_quota_set(self, project, usage=False):
        # openstacksdk's normalised shape: `<resource>` = limit,
        # `usage[<resource>]` = in_use.
        a = self._absolute
        return types.SimpleNamespace(
            volumes=a.max_total_volumes,
            gigabytes=a.max_total_volume_gigabytes,
            usage={"volumes": a.total_volumes_used, "gigabytes": a.total_gigabytes_used},
        )

    def backend_pools(self):
        if self._pools is None:
            raise RuntimeError("backend pools need an admin credential")
        return self._pools


class _FakeIdentityProxy:
    def __init__(self, projects):
        self._projects = projects

    def projects(self):
        return self._projects


def _project(id_, name, tags=None):
    return types.SimpleNamespace(id=id_, name=name, tags=tags or [])


def _hypervisor(vcpus=8, memory_mb=16384, status="enabled"):
    # openstacksdk's Hypervisor maps Nova's `memory_mb` onto `memory_size`.
    return types.SimpleNamespace(vcpus=vcpus, memory_size=memory_mb, status=status)


def _pool(total_gb=1000):
    return types.SimpleNamespace(name="storage@lvm#LVM", capabilities={"total_capacity_gb": total_gb})


# Two 8-vCPU / 16 GB compute nodes + a 1 TB Cinder pool.
DEFAULT_HYPERVISORS = [_hypervisor(), _hypervisor()]
DEFAULT_POOLS = [_pool()]


def _fake_conn(compute_absolute, block_storage_absolute, projects, hypervisors=DEFAULT_HYPERVISORS, pools=DEFAULT_POOLS):
    return types.SimpleNamespace(
        identity=_FakeIdentityProxy(projects),
        compute=_FakeComputeProxy(compute_absolute, hypervisors),
        block_storage=_FakeBlockStorageProxy(block_storage_absolute, pools),
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
    # Low quota usage (well under warning ratio) but the cost of what is
    # reserved (4 vCPUs / 8 GB / 40 GB on this fake 16-vCPU cloud) still
    # exceeds the tiny configured budget.
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


# --- compute capacity discovery tiers (Nova -> Placement -> Prometheus) -----

def _cfg():
    from app.services import finops

    return finops.load_config()


def _placement_conn(providers):
    """`providers`: {rp_id: {"VCPU": n, "MEMORY_MB": n}}"""
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [], hypervisors=None)
    conn.placement = types.SimpleNamespace(
        resource_providers=lambda: [types.SimpleNamespace(id=k) for k in providers],
        resource_provider_inventories=lambda rp: [
            types.SimpleNamespace(resource_class=c, total=t) for c, t in providers[rp.id].items()
        ],
    )
    return conn


def test_capacity_falls_back_to_placement_when_nova_has_no_vcpu_fields(monkeypatch):
    """Nova >= 2.88 drops vcpus/memory_mb from the hypervisor API."""
    monkeypatch.setenv("FINOPS_CAPACITY_FROM_PROMETHEUS", "false")
    conn = _placement_conn({"a": {"VCPU": 16, "MEMORY_MB": 32768}, "b": {"VCPU": 8, "MEMORY_MB": 16384}, "disk": {"DISK_GB": 100}})
    conn.compute.hypervisors = lambda details=False: [types.SimpleNamespace(status="enabled", vcpus=None, memory_size=None)]

    cap = qbm._fetch_cloud_capacity(conn, _cfg())

    assert (cap.physical_vcpus, cap.physical_ram_mb, cap.compute_nodes) == (24.0, 49152.0, 2)
    assert cap.source["vcpus"] == "placement"
    assert not any("Compute capacity unknown" in w for w in cap.warnings)


def test_capacity_falls_back_to_prometheus_and_reports_why_earlier_tiers_failed(monkeypatch):
    monkeypatch.setenv("FINOPS_CAPACITY_FROM_PROMETHEUS", "true")
    answers = {
        "node_cpu_seconds_total": [{"value": [0, "12"]}],
        "sum(node_memory": [{"value": [0, str(16 * 1024**3)]}],
        "count(count by": [{"value": [0, "2"]}],
    }
    import app.services.prometheus_client as pc

    monkeypatch.setattr(pc, "query", lambda q, timeout=5: next(v for k, v in answers.items() if k in q))
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [], hypervisors=None)  # no .placement either

    cap = qbm._fetch_cloud_capacity(conn, _cfg())

    assert cap.physical_vcpus == 12.0 and cap.physical_ram_mb == 16384.0 and cap.compute_nodes == 2
    assert cap.source["vcpus"] == "prometheus"


def test_unknown_capacity_warning_lists_the_reasons(monkeypatch):
    monkeypatch.setenv("FINOPS_CAPACITY_FROM_PROMETHEUS", "false")
    monkeypatch.delenv("FINOPS_FALLBACK_PHYSICAL_VCPUS", raising=False)
    monkeypatch.delenv("FINOPS_FALLBACK_PHYSICAL_RAM_GB", raising=False)
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [], hypervisors=None)

    cap = qbm._fetch_cloud_capacity(conn, _cfg())

    warning = next(w for w in cap.warnings if "Compute capacity unknown" in w)
    assert "Nova hypervisors: RuntimeError" in warning and "Placement:" in warning
