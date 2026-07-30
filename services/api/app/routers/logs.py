import logging
import time

from fastapi import APIRouter, HTTPException, Query

from ..services import loki_client

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

logger = logging.getLogger(__name__)

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _escape(value: str) -> str:
    """Escape a value for safe interpolation inside a LogQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_selector(host: str, source: str) -> str:
    matchers = []

    if host and host != "all":
        matchers.append(f'host="{_escape(host)}"')
    else:
        # Loki requires at least one matcher that can't match the empty
        # string; `=~".+"` is the standard "match everything" trick.
        matchers.append('host=~".+"')

    if source and source != "all":
        # `job` is set by Promtail to either "system" (syslog) or the
        # service name for per-service log files, so it doubles as a
        # unified "source" filter across both kinds of streams.
        matchers.append(f'job="{_escape(source)}"')

    return "{" + ", ".join(matchers) + "}"


@router.get("")
def get_logs(
    host: str = Query("all"),
    source: str = Query("all"),
    level: str = Query("all"),
    q: str = Query("", description="free-text search within the log line"),
    minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(500, ge=1, le=5000),
):
    logql = _build_selector(host, source)

    if level and level.upper() != "ALL" and level.upper() in LEVELS:
        logql += f' |= "{_escape(level.upper())}"'

    if q:
        logql += f' |= "{_escape(q)}"'

    end = time.time()
    start = end - minutes * 60

    try:
        streams = loki_client.query_range(logql, start, end, limit=limit)
    except Exception:
        logger.exception("error querying Loki for logs")
        raise HTTPException(status_code=502, detail="failed to fetch logs from Loki")

    entries = []
    for stream in streams:
        labels = stream.get("stream", {})
        for ts_ns, line in stream.get("values", []):
            entries.append({
                "ts": int(ts_ns) // 1_000_000,  # ms, for easy use in JS Date()
                "line": line,
                "host": labels.get("host"),
                "role": labels.get("role"),
                "source": labels.get("job"),
                "service": labels.get("service"),
            })

    entries.sort(key=lambda e: e["ts"], reverse=True)
    return entries[:limit]


@router.get("/hosts")
def get_hosts():
    try:
        return sorted(loki_client.label_values("host"))
    except Exception:
        logger.exception("error querying Loki for host label values")
        raise HTTPException(status_code=502, detail="failed to fetch hosts from Loki")


@router.get("/sources")
def get_sources():
    try:
        return sorted(loki_client.label_values("job"))
    except Exception:
        logger.exception("error querying Loki for job label values")
        raise HTTPException(status_code=502, detail="failed to fetch sources from Loki")
