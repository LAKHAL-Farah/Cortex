import os
import requests

PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL",
    "http://prometheus:9090/api/v1/query"
)
RANGE_URL = os.getenv("PROMETHEUS_RANGE_URL", "http://prometheus:9090/api/v1/query_range")


def query(promql):

    response = requests.get(
        PROMETHEUS_URL,
        params={
            "query": promql
        },
        timeout=5
    )

    response.raise_for_status()

    data = response.json()

    if data.get("status") != "success":
        raise Exception(f"Prometheus query failed: {data}")

    return data["data"]["result"]


def query_range(promql: str, start: float, end: float, step: str = "15s"):
    response = requests.get(
        RANGE_URL,
        params={"query": promql, "start": start, "end": end, "step": step},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        raise Exception(f"Prometheus range query failed: {data}")
    return data["data"]["result"]