import requests


PROMETHEUS_URL = "http://10.0.1.10:9090/api/v1/query"



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
