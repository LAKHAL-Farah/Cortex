import re
import time
from dataclasses import dataclass

from . import loki_client


FAILED_METRIC_NAME = "ssh_failed_logins_5min"
SUCCESS_METRIC_NAME = "ssh_successful_logins_5min"
WINDOW_SECONDS = 5 * 60

_FAILED_QUERY = ' |= "Failed password"'
_SUCCESS_QUERY = ' |= "Accepted "'
_SOURCE_IP_PATTERN = re.compile(r"\bfrom\s+(\S+)")


@dataclass(frozen=True)
class Stats:
    count: int
    source_ips: list[str] | None


def _query_stats(hostname: str, line_filter: str) -> Stats:
    escaped_hostname = hostname.replace("\\", "\\\\").replace('"', '\\"')
    logql = f'{{host="{escaped_hostname}"}}{line_filter}'
    end = time.time()
    streams = loki_client.query_range(
        logql,
        end - WINDOW_SECONDS,
        end,
        limit=500,
    )

    count = 0
    source_ips: set[str] = set()
    for stream in streams:
        for _, line in stream.get("values", []):
            count += 1
            match = _SOURCE_IP_PATTERN.search(line)
            if match:
                source_ips.add(match.group(1).rstrip(".,;"))

    return Stats(count=count, source_ips=sorted(source_ips) or None)


def get_failed_login_stats(hostname: str) -> Stats:
    return _query_stats(hostname, _FAILED_QUERY)


def get_successful_login_stats(hostname: str) -> Stats:
    return _query_stats(hostname, _SUCCESS_QUERY)