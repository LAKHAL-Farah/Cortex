import json
import os
import tempfile
from sqlalchemy.orm import Session
from .. import crud

FILE_SD_PATH = os.environ.get("PROMETHEUS_FILE_SD_PATH", "/file_sd/nodes.json")


def regenerate_file_sd(db: Session) -> None:
    """Rewrite the Prometheus file_sd target file from the current `nodes` table."""
    nodes = crud.list_nodes(db)
    targets = [
        {
            "targets": [f"{n.ip_address}:{n.exporter_port}"],
            "labels": {"role": n.role, "node": n.hostname},
        }
        for n in nodes
        if n.is_active
    ]

    dir_name = os.path.dirname(FILE_SD_PATH)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(targets, f, indent=2)
    os.chmod(tmp_path, 0o644)          # NEW — mkstemp defaults to 0600
    os.replace(tmp_path, FILE_SD_PATH)