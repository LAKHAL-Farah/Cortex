import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services.node_seeder import seed_nodes_from_file_sd

# Same shape as the real /etc/prometheus/file_sd/nodes.json written by
# infra/ansible/roles/prometheus/templates/nodes.json.j2
NODES_JSON = [
    {"targets": ["10.0.1.10:9100"], "labels": {"node": "controller", "role": "controller"}},
    {"targets": ["10.0.1.2:9100"], "labels": {"node": "compute1", "role": "compute"}},
    {"targets": ["10.0.1.4:9100"], "labels": {"node": "compute2", "role": "compute"}},
    {"targets": ["10.0.2.3:9100"], "labels": {"node": "storage", "role": "storage"}},
]


def _db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _write(tmp_path, entries):
    p = tmp_path / "nodes.json"
    p.write_text(json.dumps(entries))
    return str(p)


def test_seeds_all_targets_from_file(tmp_path):
    db = _db()
    path = _write(tmp_path, NODES_JSON)

    seeded = seed_nodes_from_file_sd(db, path=path)

    assert seeded == 4
    hostnames = {n.hostname for n in db.query(models.Node).all()}
    assert hostnames == {"controller", "compute1", "compute2", "storage"}

    storage = db.query(models.Node).filter_by(hostname="storage").one()
    assert storage.ip_address == "10.0.2.3"
    assert storage.role == "storage"
    assert storage.exporter_port == 9100
    assert storage.is_active is True


def test_does_not_seed_when_table_already_has_nodes(tmp_path):
    db = _db()
    db.add(models.Node(hostname="existing", ip_address="10.0.1.99", role="compute"))
    db.commit()
    path = _write(tmp_path, NODES_JSON)

    seeded = seed_nodes_from_file_sd(db, path=path)

    assert seeded == 0
    assert db.query(models.Node).count() == 1


def test_missing_file_is_a_noop(tmp_path):
    db = _db()

    seeded = seed_nodes_from_file_sd(db, path=str(tmp_path / "does-not-exist.json"))

    assert seeded == 0
    assert db.query(models.Node).count() == 0


def test_skips_malformed_entries_but_seeds_the_rest(tmp_path):
    db = _db()
    entries = NODES_JSON + [{"targets": [], "labels": {"node": "broken"}}]
    path = _write(tmp_path, entries)

    seeded = seed_nodes_from_file_sd(db, path=path)

    assert seeded == 4
    assert db.query(models.Node).filter_by(hostname="broken").first() is None
