#!/bin/sh
# services/api/entrypoint.sh
#
# Previously the container's CMD went straight to uvicorn, so
# `alembic upgrade head` had to be run by hand after every fresh
# `docker-compose up` before the API actually worked -- the `nodes`,
# `baselines`, etc. tables simply didn't exist yet. Node seeding
# (app/services/node_seeder.py) already runs automatically on FastAPI
# startup, but it no-ops (and swallows the error) when those tables are
# missing, so it silently never seeded anything until someone remembered
# to run migrations *and* restart the container afterwards.
#
# This wraps the real startup command: wait for Postgres to actually be
# accepting connections (not just "container started" -- initdb on a brand
# new volume takes a moment), run migrations, then hand off to uvicorn. From
# then on the existing startup seeding just works, with no manual step.
set -e

echo "cortex-api: waiting for the database..."
python - <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
engine = create_engine(url)

for attempt in range(30):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        break
    except Exception as exc:
        print(f"cortex-api: database not ready yet ({exc}); retrying in 2s...", flush=True)
        time.sleep(2)
else:
    print("cortex-api: database never became ready, giving up", file=sys.stderr)
    sys.exit(1)
PY

echo "cortex-api: running migrations..."
alembic upgrade head

echo "cortex-api: starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
