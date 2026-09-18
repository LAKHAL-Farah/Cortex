"""Feedback-loop endpoints for the OpenStack Expert Agent (v1.0, see
docs/architecture/adr-0010-openstack-expert-v1-expansion.md): turning a
resolved incident into a new catalog entry from the UI, the way the
original planning doc's §4 ("how the catalog grows") describes.

Every route here is a plain CRUD/workflow layer over models.CatalogEntry
(via crud.py) -- the actual "does this entry make sense" logic lives in
agents/nodes/openstack_expert_catalog_store.py's `validate_symptom_entry`,
reused as-is by the /publish route below rather than re-implemented here.
"""
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..agents.nodes.openstack_expert_catalog_store import validate_symptom_entry
from ..auth import get_current_user, require_admin
from ..db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/openstack-expert/catalog-entries", tags=["openstack-expert"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(*parts: str) -> str:
    """Turns e.g. ("compute-02", "cpu_usage") into something like
    "compute-02-cpu-usage-a1b2c3" -- lowercase, hyphen-separated, with a
    short random suffix so two drafts seeded from the same
    (hostname, metric_name) never collide on CatalogEntry.symptom_id's
    unique constraint. Matches CatalogEntryCreate.symptom_id's own
    pattern (`^[a-z0-9][a-z0-9-]*$`)."""
    base = "-".join(parts).lower()
    base = _SLUG_RE.sub("-", base).strip("-")
    suffix = uuid.uuid4().hex[:6]
    return f"{base}-{suffix}" if base else f"catalog-entry-{suffix}"


def _get_entry_or_404(db: Session, entry_id: uuid.UUID) -> models.CatalogEntry:
    entry = crud.get_catalog_entry(db, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "catalog entry not found")
    return entry


def _require_draft(entry: models.CatalogEntry) -> None:
    if entry.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"catalog entry is {entry.status!r}, not draft -- only a draft can be edited "
            "(archive it and start a new draft to revise a published entry)",
        )


@router.post("/draft-from-incident", response_model=schemas.CatalogEntryOut, status_code=status.HTTP_201_CREATED)
def draft_catalog_entry_from_incident(
    payload: schemas.CatalogEntryDraftFromIncident,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """"Save this as a catalog entry" on a resolved incident -- pre-fills
    a new draft from the most recently resolved `AnomalyEvent` for this
    (hostname, metric_name), seeding `what_it_means` from the operator's
    own resolution note. The draft is invisible to the live agent
    (`_match_symptoms`) until an admin fills in commands/category and
    publishes it -- see models.CatalogEntry's docstring on why."""
    event = crud.get_latest_resolved_anomaly_event(db, payload.hostname, payload.metric_name)
    if event is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"no resolved incident found for {payload.hostname}/{payload.metric_name}",
        )

    entry = crud.create_catalog_entry(
        db,
        symptom_id=_slugify(payload.hostname, payload.metric_name),
        title=f"{payload.metric_name} on {payload.hostname}",
        # "host" is a safe, always-valid generic default (see
        # openstack_expert_catalog.Category) -- the operator narrows it
        # before publishing; validate_symptom_entry only checks the final
        # value is *some* valid category, not that it's the "right" one.
        category="host",
        what_it_means=event.resolution_note or "",
        metric_names=[payload.metric_name],
        source_hostname=payload.hostname,
        source_metric_name=payload.metric_name,
        source_anomaly_event_id=event.id,
        created_by=current_user.id,
    )
    return entry


@router.post("", response_model=schemas.CatalogEntryOut, status_code=status.HTTP_201_CREATED)
def create_catalog_entry(
    payload: schemas.CatalogEntryCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a blank draft with no source incident -- see
    schemas.CatalogEntryCreate's docstring."""
    if crud.get_catalog_entry_by_symptom_id(db, payload.symptom_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"symptom_id {payload.symptom_id!r} already exists")
    entry = crud.create_catalog_entry(
        db,
        symptom_id=payload.symptom_id,
        title=payload.title,
        category=payload.category,
        created_by=current_user.id,
    )
    return entry


@router.get("", response_model=list[schemas.CatalogEntryOut])
def list_catalog_entries(
    status_filter: schemas.CatalogEntryStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
):
    return crud.list_catalog_entries(db, status=status_filter.value if status_filter else None)


@router.get("/{entry_id}", response_model=schemas.CatalogEntryOut)
def get_catalog_entry(entry_id: uuid.UUID, db: Session = Depends(get_db)):
    return _get_entry_or_404(db, entry_id)


@router.put("/{entry_id}", response_model=schemas.CatalogEntryOut)
def update_catalog_entry(entry_id: uuid.UUID, payload: schemas.CatalogEntryUpdate, db: Session = Depends(get_db)):
    entry = _get_entry_or_404(db, entry_id)
    _require_draft(entry)
    return crud.update_catalog_entry(db, entry, payload)


@router.post("/{entry_id}/publish", response_model=schemas.CatalogEntryOut, dependencies=[Depends(require_admin)])
def publish_catalog_entry(entry_id: uuid.UUID, db: Session = Depends(get_db)):
    """Admin-gated: this is what makes a submitted entry participate in
    `_match_symptoms` for every user (agents/nodes/openstack_expert.py's
    `_combined_catalog`), same trust boundary as the official-docs
    /ingest endpoint -- see models.CatalogEntry's docstring on why an
    unreviewed entry is a real safety concern, not just a quality one."""
    entry = _get_entry_or_404(db, entry_id)
    _require_draft(entry)

    errors = validate_symptom_entry({
        "id": entry.symptom_id,
        "title": entry.title,
        "category": entry.category,
        "confirm_commands": entry.confirm_commands,
        "remediation_commands": entry.remediation_commands,
        "what_it_means": entry.what_it_means,
    })
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=schemas.CatalogEntryValidation(errors=errors).model_dump(),
        )
    return crud.publish_catalog_entry(db, entry)


@router.post("/{entry_id}/archive", response_model=schemas.CatalogEntryOut, dependencies=[Depends(require_admin)])
def archive_catalog_entry(entry_id: uuid.UUID, db: Session = Depends(get_db)):
    entry = _get_entry_or_404(db, entry_id)
    return crud.archive_catalog_entry(db, entry)
