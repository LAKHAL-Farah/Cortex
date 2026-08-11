#!/bin/sh
set -e

echo "cortex-api: waiting for database..."

python - <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

engine = create_engine(
    os.environ["DATABASE_URL"],
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
)

for attempt in range(30):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("cortex-api: database ready")
        break
    except Exception:
        print(
            f"cortex-api: database not ready "
            f"(attempt {attempt + 1}/30), retrying...",
            flush=True,
        )
        time.sleep(2)
else:
    print("cortex-api: database unavailable", file=sys.stderr)
    sys.exit(1)

engine.dispose()
PY

echo "cortex-api: running migrations..."
alembic upgrade head

echo "cortex-api: starting server..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000
