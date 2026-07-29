import os
import requests

# Default matches the production controller (infra/ansible/inventory/hosts.ini: 10.0.1.10).
# Override for the docker-compose sandbox, where Prometheus runs as its own container
# reachable by service name, e.g. PROMETHEUS_URL=http://prometheus:9090/api/v1/query
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://10.0.1.10:9090/api/v1/query")



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


    return data["data"]["result"]
