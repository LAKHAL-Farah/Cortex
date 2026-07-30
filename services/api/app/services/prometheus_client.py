import os
import requests

PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL",
    "http://prometheus:9090/api/v1/query"
)


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