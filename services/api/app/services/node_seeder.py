"""Bootstraps the `nodes` table from an existing Prometheus file_sd target
file the first time the API starts against an empty database.

Why this exists: infra provisioning (infra/ansible/roles/prometheus) writes
`{{ prometheus_file_sd_dir }}/nodes.json` (== `/etc/prometheus/file_sd/nodes.json`
on the real hosts) straight from the static Ansible inventory the moment the
stack is provisioned -- see roles/prometheus/templates/nodes.json.j2 -- and
that happens *before* the Cortex API/DB is ever involved. So it's entirely
normal for Prometheus to already be scraping real targets while the `nodes`
table is completely empty: the /nodes page and dashboard are backed by the
DB, not by that file, so they showed nothing even though monitoring itself
was working fine.
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .. import crud, schemas
from . import prometheus_sd

logger = logging.getLogger(__name__)


def seed_nodes_from_file_sd(db: Session, path: str | None = None) -> int:
    """Insert one Node row per file_sd target -- but only when the table is
    still empty.

    This is a one-time bootstrap, not an ongoing sync: it no-ops as soon as
    a single node exists (whether from a previous seed run or a manual
    add), so it never overwrites/duplicates nodes added, edited, or removed
    through the API afterwards. After the first successful boot, the file
    is *generated from* the DB (see prometheus_sd.regenerate_file_sd), not
    the other way around.

    Returns the number of nodes inserted (0 if the table already had rows,
    the file is missing, or the file couldn't be parsed).
    """
    if crud.list_nodes(db):
        return 0

    file_sd_path = Path(path) if path is not None else Path(prometheus_sd.FILE_SD_PATH)
    if not file_sd_path.exists():
        logger.info("node seed: %s not found, nothing to seed", file_sd_path)
        return 0

    try:
        entries = json.loads(file_sd_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("node seed: could not read/parse %s", file_sd_path)
        return 0

    seeded = 0
    for entry in entries:
        labels = entry.get("labels") or {}
        hostname = labels.get("node")
        role = labels.get("role")
        targets = entry.get("targets") or []

        if not hostname or not role or not targets:
            logger.warning("node seed: skipping malformed entry %r", entry)
            continue

        ip, _, port = targets[0].partition(":")
        try:
            payload = schemas.NodeCreate(
                hostname=hostname,
                ip_address=ip,
                role=role,
                exporter_port=int(port) if port else 9100,
                is_active=True,
            )
        except Exception:
            logger.exception("node seed: skipping invalid entry for %r", hostname)
            continue

        try:
            crud.create_node(db, payload)
            seeded += 1
        except crud.DuplicateNodeError:
            logger.warning("node seed: %s already registered, skipping", hostname)

    logger.info("node seed: inserted %d node(s) from %s", seeded, file_sd_path)
    return seeded
