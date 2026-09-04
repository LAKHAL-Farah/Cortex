"""Resolves which known node (if any) a natural-language question refers
to. Shared by every agent that needs a node before it can do anything else
(monitoring, prediction) so the matching logic -- and its quality -- lives
in exactly one place.

Two-tier, cheapest-first:
1. An exact/substring pass: handles the common case (the hostname spelled
   out verbatim, or with a space instead of a hyphen) without ever calling
   an LLM. Free, deterministic, and unambiguous when it hits.
2. If that doesn't land on exactly one node, an LLM pass takes over: given
   the literal list of known hostnames, it's asked which one (if any) the
   question is about. This is what actually handles a *partial* mention
   ("compute1" for "compute1-sim") or a *deformed*/misspelled one
   ("compue-02", "the controler node") -- patterns a regex/substring match
   can't generalize to, but that a model reading the sentence naturally
   can. The LLM is constrained to pick from the given list (never allowed
   to invent a hostname), and its answer is re-checked against that list
   before being trusted, so a hallucinated hostname can't leak through.

If neither tier lands on exactly one node -- including because the LLM
isn't configured or the call fails -- there's a third, last-resort tier
(v0.8, session memory): if the caller passes `session_memory` and it has a
`last_node` from an earlier turn in the same conversation, and that
hostname is still present in the *current* `known_nodes` (re-validated
against the Living Model, not trusted blindly -- a node removed from
topology since that earlier turn should not still resolve), that node is
returned. This is what lets a bare follow-up like "what about now?" -- no
hostname in it at all -- resolve to "the node we were just talking about"
instead of falling through to "I couldn't tell which node you meant" on
every single-word follow-up in a conversation. Still returns None (not a
guess) if there's no session memory to fall back on either.
"""
import logging
import re

from pydantic import BaseModel, Field

from ..services.llm_client import LLMConfigError, get_chat_model
from .state import KnownNode

logger = logging.getLogger(__name__)

_HOSTNAME_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]*")


class _NodeResolution(BaseModel):
    hostname: str | None = Field(
        description=(
            "The exact hostname from the provided list that the question is "
            "about, copied verbatim from that list -- even if the question "
            "spelled it differently (partial, misspelled, a space instead of "
            "a hyphen, different casing, etc). Null if the question doesn't "
            "clearly refer to exactly one of the listed nodes."
        )
    )


def _dedupe(nodes: list[KnownNode]) -> list[KnownNode]:
    seen: set[str] = set()
    unique: list[KnownNode] = []
    for node in nodes:
        if node["hostname"] not in seen:
            seen.add(node["hostname"])
            unique.append(node)
    return unique


def _exact_match(query: str, known_nodes: list[KnownNode]) -> KnownNode | None:
    query_lower = query.lower()
    tokens = set(_HOSTNAME_TOKEN.findall(query_lower))
    # Normalize "compute 02" -> "compute-02" so a space where the hostname
    # has a hyphen still matches.
    normalized_query = "-".join(t for t in re.split(r"\s+", query_lower.strip()) if t)

    matches = [
        node
        for node in known_nodes
        if node["hostname"] in tokens
        or node["hostname"].replace("-", "") in normalized_query.replace("-", "")
        or node["hostname"] in query_lower
    ]
    unique_matches = _dedupe(matches)
    if len(unique_matches) == 1:
        return unique_matches[0]
    return None


def _llm_match(query: str, known_nodes: list[KnownNode]) -> KnownNode | None:
    hostnames = [n["hostname"] for n in known_nodes]
    try:
        llm = get_chat_model(temperature=0, tier="fast")
        structured = llm.with_structured_output(_NodeResolution)
        result = structured.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "You match a question to one node hostname from a fixed list. "
                        "Known nodes: " + ", ".join(hostnames)
                    ),
                },
                {"role": "user", "content": query},
            ]
        )
    except LLMConfigError:
        logger.info("node_resolver: LLM not configured, no fuzzy match attempted")
        return None
    except Exception:
        # Includes structured-output parsing failures (a model that
        # doesn't reliably conform to _NodeResolution's schema raises
        # here, it doesn't return hostname=None) -- logged at warning, not
        # info, since this is the "the model itself is unreliable" case,
        # not the routine "no node mentioned" case below.
        logger.warning("node_resolver: LLM node resolution call failed", exc_info=True)
        return None

    if not result.hostname:
        logger.info("node_resolver: LLM found no matching node in query %r", query)
        return None
    for node in known_nodes:
        if node["hostname"] == result.hostname:
            return node
    # Model returned something outside the given list -- don't trust a
    # hallucinated hostname, treat it the same as "no confident match".
    logger.warning(
        "node_resolver: LLM returned hostname %r not in known list, ignoring",
        result.hostname,
    )
    return None


def _session_memory_match(known_nodes: list[KnownNode], session_memory: dict | None) -> KnownNode | None:
    if not session_memory:
        return None
    last_node = session_memory.get("last_node")
    if not last_node or not last_node.get("hostname"):
        return None
    for node in known_nodes:
        if node["hostname"] == last_node["hostname"]:
            return node
    # The remembered hostname isn't in today's Living Model anymore
    # (renamed/decommissioned since that earlier turn) -- don't resolve to
    # a node that no longer exists.
    return None


def resolve_node(
    query: str,
    known_nodes: list[KnownNode],
    session_memory: dict | None = None,
) -> KnownNode | None:
    if not known_nodes:
        return None
    if len(known_nodes) == 1:
        # Only one node registered at all -- no ambiguity possible even if
        # the query never names it explicitly.
        return known_nodes[0]

    node = _exact_match(query, known_nodes)
    if node is not None:
        return node

    node = _llm_match(query, known_nodes)
    if node is not None:
        return node

    return _session_memory_match(known_nodes, session_memory)
