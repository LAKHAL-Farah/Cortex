"""Community-sourced web search fallback for the OpenStack Expert Agent
(v1.0, see docs/architecture/adr-0010-openstack-expert-v1-expansion.md) --
the last tier tried when a standalone question matches neither the
curated catalog (agents/nodes/openstack_expert_catalog.py) nor the
official-docs index (services/openstack_docs/). Per the original planning
doc's §2/§6: "live web_search fallback for Launchpad bugs / mailing list
threads... always labeled as community-sourced, distinct from
authoritative docs... use only as the low-confidence fallback, never
primary."

Same configurable-endpoint-with-graceful-degradation shape as every other
external HTTP client in this codebase (services/cve_feed.py's package
inventory client, ebpf_signal.py, loki_client.py): a missing/unreachable
provider raises `WebSearchError`, which the caller (openstack_expert.py)
catches broadly and degrades from -- "no web results" is never a crash,
and never blocks the rest of the answer.
"""
import os
from dataclasses import dataclass

import requests

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
TAVILY_SEARCH_URL = os.environ.get("CORTEX_WEB_SEARCH_URL", "https://api.tavily.com/search")
REQUEST_TIMEOUT_SECONDS = 12
_MAX_SNIPPET_CHARS = 400

# Biased toward the OpenStack community's own bug trackers / mailing
# lists / Q&A -- deliberately NOT docs.openstack.org, which is the
# *official* tier (services/openstack_docs/) and is kept as a separate
# search entirely so the two result sets never get conflated -- see
# adr-0008 §1's table on why "internal vs official vs community"
# citations have to stay distinguishable, and adr-0010 Decision 2 for
# why this tier specifically excludes the official-docs domain.
COMMUNITY_DOMAINS = [
    "bugs.launchpad.net",
    "lists.openstack.org",
    "ask.openstack.org",
    "storyboard.openstack.org",
    "stackoverflow.com",
    "serverfault.com",
]


class WebSearchError(RuntimeError):
    """The web-search tier isn't usable right now -- no API key
    configured, or the provider couldn't be reached. Callers treat this
    identically either way: fall through to the next fallback tier (or
    the final static answer), never a crash."""


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str


def search(query: str, max_results: int = 5) -> list[WebSearchResult]:
    if not TAVILY_API_KEY:
        raise WebSearchError("TAVILY_API_KEY is not set -- web search fallback is unavailable")

    payload = {
        "api_key": TAVILY_API_KEY,
        # "OpenStack" prefixed on every query -- this fallback only ever
        # fires from openstack_expert.py, and biasing the query itself
        # (not just include_domains) keeps results relevant even on a
        # domain in COMMUNITY_DOMAINS that hosts plenty of non-OpenStack
        # content too (e.g. Stack Overflow).
        "query": f"OpenStack {query}",
        "search_depth": "basic",
        "include_domains": COMMUNITY_DOMAINS,
        "max_results": max_results,
    }
    try:
        response = requests.post(TAVILY_SEARCH_URL, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WebSearchError(f"web search request failed: {exc}") from exc

    data = response.json()
    results: list[WebSearchResult] = []
    for item in data.get("results", []):
        url = (item.get("url") or "").strip()
        if not url:
            continue
        title = (item.get("title") or "").strip() or url
        snippet = (item.get("content") or "").strip()[:_MAX_SNIPPET_CHARS]
        results.append(WebSearchResult(title=title, url=url, snippet=snippet))
    return results[:max_results]
