"""Tests for agents/nodes/openstack_expert_catalog_store.py.

`validate_symptom_entry` is pure -- no DB, no network -- and tests the
exact same invariants tests/test_openstack_expert_catalog.py enforces on
the static CATALOG (see that module's own docstring for why they're
duplicated rather than shared). `row_to_symptom_entry`/
`load_published_entries` need a real Postgres (SessionLocal), same as
agents/nodes/anomaly.py's own DB-backed helpers -- see
tests/test_anomalies_router.py for the precedent of seeding/cleaning
directly via SessionLocal rather than mocking the ORM.
"""
import uuid

import pytest

from app import crud, models
from app.agents.nodes import openstack_expert_catalog_store as store
from app.db import SessionLocal

GOOD_ENTRY = {
    "id": "test-symptom",
    "title": "Test symptom",
    "category": "compute",
    "what_it_means": "Something is wrong in a way this test made up.",
    "confirm_commands": [
        {"command": "openstack compute service list", "description": "check status", "read_only": True},
    ],
    "remediation_commands": [
        {"command": "systemctl restart nova-compute", "description": "restart it", "read_only": False},
    ],
}


# ------------------------------------------------------- validate_symptom_entry --

def test_valid_entry_has_no_errors():
    assert store.validate_symptom_entry(GOOD_ENTRY) == []


def test_missing_title_is_an_error():
    entry = {**GOOD_ENTRY, "title": ""}
    assert any("title" in e for e in store.validate_symptom_entry(entry))


def test_invalid_category_is_an_error():
    entry = {**GOOD_ENTRY, "category": "not-a-real-category"}
    assert any("category" in e for e in store.validate_symptom_entry(entry))


def test_no_confirm_commands_is_an_error():
    entry = {**GOOD_ENTRY, "confirm_commands": []}
    assert any("confirm_command" in e for e in store.validate_symptom_entry(entry))


def test_no_remediation_commands_is_an_error():
    entry = {**GOOD_ENTRY, "remediation_commands": []}
    assert any("remediation_command" in e for e in store.validate_symptom_entry(entry))


def test_confirm_command_that_is_not_read_only_is_an_error():
    entry = {**GOOD_ENTRY, "confirm_commands": [
        {"command": "cinder-manage volume reset_state x", "description": "reset it", "read_only": False},
    ]}
    errors = store.validate_symptom_entry(entry)
    assert any("read_only=True" in e for e in errors)


def test_command_missing_description_is_an_error():
    entry = {**GOOD_ENTRY, "confirm_commands": [
        {"command": "openstack volume show x", "description": "", "read_only": True},
    ]}
    assert any("description" in e for e in store.validate_symptom_entry(entry))


def test_command_missing_read_only_bool_is_an_error():
    entry = {**GOOD_ENTRY, "confirm_commands": [{"command": "x", "description": "y"}]}
    assert any("read_only bool" in e for e in store.validate_symptom_entry(entry))


def test_remediation_with_only_read_only_commands_is_an_error():
    entry = {**GOOD_ENTRY, "remediation_commands": [
        {"command": "openstack volume show x", "description": "just checking", "read_only": True},
    ]}
    errors = store.validate_symptom_entry(entry)
    assert any("no state-changing command" in e for e in errors)


def test_missing_what_it_means_is_an_error():
    entry = {**GOOD_ENTRY, "what_it_means": ""}
    assert any("what_it_means" in e for e in store.validate_symptom_entry(entry))


def test_multiple_problems_are_all_reported_at_once():
    errors = store.validate_symptom_entry({})
    # title, category, confirm_commands, remediation_commands, what_it_means
    assert len(errors) >= 5


def test_never_raises_on_a_completely_malformed_entry():
    # No exception, just a (long) list of errors.
    store.validate_symptom_entry({"confirm_commands": [{}], "remediation_commands": [{}]})


# --------------------------------------------------------------- DB-backed --

def _cleanup(symptom_id: str):
    db = SessionLocal()
    try:
        row = crud.get_catalog_entry_by_symptom_id(db, symptom_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


@pytest.fixture
def draft_entry():
    db = SessionLocal()
    try:
        entry = crud.create_catalog_entry(
            db,
            symptom_id=f"pytest-{uuid.uuid4().hex[:8]}",
            title="Pytest draft entry",
            category="compute",
            what_it_means="Made up for a test.",
            confirm_commands=[{"command": "echo hi", "description": "d", "read_only": True}],
            remediation_commands=[{"command": "echo restart", "description": "d", "read_only": False}],
        )
        symptom_id = entry.symptom_id
    finally:
        db.close()
    yield symptom_id
    _cleanup(symptom_id)


def test_row_to_symptom_entry_matches_symptom_entry_shape(draft_entry):
    db = SessionLocal()
    try:
        row = crud.get_catalog_entry_by_symptom_id(db, draft_entry)
        entry = store.row_to_symptom_entry(row)
    finally:
        db.close()
    assert entry["id"] == draft_entry
    assert entry["title"] == "Pytest draft entry"
    assert entry["confirm_commands"][0]["command"] == "echo hi"


def test_load_published_entries_excludes_drafts(draft_entry):
    # Never published -- load_published_entries must not return it.
    entries = store.load_published_entries()
    assert draft_entry not in [e["id"] for e in entries]


def test_load_published_entries_includes_published_rows(draft_entry):
    db = SessionLocal()
    try:
        row = crud.get_catalog_entry_by_symptom_id(db, draft_entry)
        crud.publish_catalog_entry(db, row)
    finally:
        db.close()

    entries = store.load_published_entries()
    assert draft_entry in [e["id"] for e in entries]


def test_load_published_entries_excludes_archived_rows(draft_entry):
    db = SessionLocal()
    try:
        row = crud.get_catalog_entry_by_symptom_id(db, draft_entry)
        crud.publish_catalog_entry(db, row)
        crud.archive_catalog_entry(db, row)
    finally:
        db.close()

    entries = store.load_published_entries()
    assert draft_entry not in [e["id"] for e in entries]


def test_load_published_entries_degrades_to_empty_list_on_db_error(monkeypatch):
    def _raise(db):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(crud, "list_published_catalog_entries", _raise)
    assert store.load_published_entries() == []
