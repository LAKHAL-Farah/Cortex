"""Bridges the DB-backed feedback-loop catalog entries (models.CatalogEntry,
v1.0, docs/architecture/adr-0010-openstack-expert-v1-expansion.md) into the
exact SymptomEntry shape openstack_expert.py's matcher and renderer already
know how to consume -- see models.CatalogEntry's own docstring for why this
is an *additive* layer over the static CATALOG in openstack_expert_catalog.py
rather than a merge into that Python literal.

Two responsibilities:

- `validate_symptom_entry` -- the exact per-entry invariants
  tests/test_openstack_expert_catalog.py already enforces on every
  hand-authored CATALOG entry (every confirm_command read-only, every
  command labeled, both layers non-empty, etc.), reused here as the gate a
  submitted entry must pass before routers/openstack_expert.py will let it
  move from "draft" to "published". A hand-authored entry gets these
  invariants for free from code review; a UI-submitted one gets them from
  this function instead -- see adr-0008 decision #5 on why that gate can't
  be skipped (an incorrect but confidently-labeled command is actively
  harmful, not just a bad sentence).
- `load_published_entries` -- opens its own short-lived DB session (same
  pattern agents/nodes/anomaly.py and agents/nodes/security.py already use
  for their own read-only DB lookups from inside a graph node -- see those
  modules' comments on why a live Session is never threaded through graph
  state) and converts every "published" row into a SymptomEntry dict.
"""
import logging

from sqlalchemy.orm import Session

from ... import crud
from ...db import SessionLocal
from ...models import CatalogEntry as CatalogEntryRow
from .openstack_expert_catalog import SymptomEntry

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {
    "compute", "storage", "network", "identity", "image",
    "message-bus", "database", "hypervisor", "host",
}


def validate_symptom_entry(entry: dict) -> list[str]:
    """Returns every problem found, so a UI can show them all at once
    rather than a fix-one-resubmit-see-the-next loop; an empty list means
    the entry is safe to publish. Never raises -- a malformed entry is
    exactly what this function exists to report, not choke on."""
    errors: list[str] = []

    if not (entry.get("title") or "").strip():
        errors.append("title is required")

    category = entry.get("category")
    if category not in _VALID_CATEGORIES:
        errors.append(f"category must be one of {sorted(_VALID_CATEGORIES)}, got {category!r}")

    confirm_commands = entry.get("confirm_commands") or []
    remediation_commands = entry.get("remediation_commands") or []

    if not confirm_commands:
        errors.append("needs at least one confirm_command (layer 2: how to confirm it yourself)")
    if not remediation_commands:
        errors.append("needs at least one remediation_command (layer 3: what's usually done about it)")

    # Core safety invariant, same as test_confirm_commands_are_always_read_only:
    # layer 2 must never suggest a state-changing command.
    for cmd in confirm_commands:
        if not cmd.get("read_only", False):
            errors.append(
                f"confirm_command {cmd.get('command', '<blank>')!r} must be read_only=True "
                "-- layer 2 must never suggest a state-changing command"
            )

    for cmd in confirm_commands + remediation_commands:
        if not isinstance(cmd.get("read_only"), bool):
            errors.append(f"command {cmd.get('command', '<blank>')!r} is missing a read_only bool")
        if not (cmd.get("description") or "").strip():
            errors.append(f"command {cmd.get('command', '<blank>')!r} is missing a description")
        if not (cmd.get("command") or "").strip():
            errors.append("a command entry has a blank command string")

    # If every remediation command were read-only it wouldn't actually be a
    # remediation section -- same reasoning as
    # test_every_remediation_section_has_at_least_one_state_changing_command.
    if remediation_commands and not any(not cmd.get("read_only", True) for cmd in remediation_commands):
        errors.append(
            "remediation_commands has no state-changing command -- if everything here is "
            "read_only it isn't actually a remediation section"
        )

    if not (entry.get("what_it_means") or "").strip():
        errors.append("what_it_means is required (layer 1: what's happening, in plain language)")

    return errors


def row_to_symptom_entry(row: CatalogEntryRow) -> SymptomEntry:
    return {
        "id": row.symptom_id,
        "title": row.title,
        "category": row.category,
        "metric_names": list(row.metric_names or []),
        "service_binaries": list(row.service_binaries or []),
        "keywords": list(row.keywords or []),
        "what_it_means": row.what_it_means or "",
        "confirm_commands": list(row.confirm_commands or []),
        "remediation_commands": list(row.remediation_commands or []),
        "doc_ref": row.doc_ref or "",
    }


def load_published_entries() -> list[SymptomEntry]:
    """Opens and closes its own SessionLocal -- see module docstring.
    Degrades to "no dynamic entries" (an empty list, never an exception)
    on a DB error, the same way anomaly.py's own SessionLocal reads
    degrade the agent to "no historical context" rather than failing the
    whole diagnosis over a transient DB hiccup -- a submitted entry not
    showing up yet is always safe; the static CATALOG still works either
    way."""
    db: Session = SessionLocal()
    try:
        rows = crud.list_published_catalog_entries(db)
        return [row_to_symptom_entry(row) for row in rows]
    except Exception:
        logger.warning("openstack_expert: failed to load published catalog entries", exc_info=True)
        return []
    finally:
        db.close()
