#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import SessionLocal
from app.services.openstack_discovery import discover_new_computes

if __name__ == "__main__":
    db = SessionLocal()
    try:
        discover_new_computes(db)
    finally:
        db.close()