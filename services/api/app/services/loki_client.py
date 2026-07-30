import os
import requests

LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")

QUERY_RANGE_URL = f"{LOKI_URL}/loki/api/v1/query_range"
LABEL_VALUES_URL = f"{LOKI_URL}/loki/api/v1/label/{{label}}/values"


def query_range(logql: str, start: float, end: float, limit: int = 500, direction: str = "backward"):
    """Run a LogQL query against Loki's /query_range endpoint and return the
    raw list of streams (each a {"stream": {labels}, "values": [[ts_ns, line], ...]})."""
    response = requests.get(
        QUERY_RANGE_URL,
        params={
            "query": logql,
            "start": _to_ns(start),
            "end": _to_ns(end),
            "limit": limit,
            "direction": direction,
        },
        timeout=10,
    )
    response.raise_for_status()

    data = response.json()

    if data.get("status") != "success":
        raise Exception(f"Loki query failed: {data}")

    return data["data"]["result"]


def label_values(label: str):
    """Return the known values for a Loki label (e.g. "host" or "job")."""
    response = requests.get(LABEL_VALUES_URL.format(label=label), timeout=10)
    response.raise_for_status()

    data = response.json()

    if data.get("status") != "success":
        raise Exception(f"Loki label query failed: {data}")

    return data["data"]


def _to_ns(unix_seconds: float) -> int:
    return int(unix_seconds * 1_000_000_000)
