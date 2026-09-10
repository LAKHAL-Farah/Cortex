"""Resolves which Neutron/Nova entity (network, subnet, or instance) a
natural-language question refers to, for question shapes that aren't about
a physical node at all -- "which VMs are on network X", "why can't
instance Y reach the internet", "is anything down on subnet Z" (v0.10,
Phase C).

node_resolver.py's exact/LLM/session-memory tiers all match against
`known_nodes` (hostnames from the Postgres node registry, a handful of
fixed strings picked at provisioning time). A network, subnet, or instance
is a different universe of names entirely -- created/renamed/deleted
through Neutron/Nova at any time, named anything a project chooses, or
referenced by its raw UUID -- and there are three *kinds* of it to
disambiguate between, not one. Reusing node_resolver's tiers against a
differently-shaped, multi-kind list would be the easy path, but a bigger,
generalized resolver ends up worse at both jobs than two focused ones --
hence this parallel module rather than widening node_resolver.py's own
matching to cover three more entity kinds it was never scoped for.

Same two-tier, cheapest-first shape as node_resolver.py, plus the same
session-memory fallback tier:

1. An exact/substring pass against every candidate's name (and, for a
   subnet, its CIDR) -- free, deterministic, unambiguous when it hits.
2. One LLM call, given every candidate grouped by kind, asked which one
   (if any) the question is about and of which kind -- constrained to
   the given lists (never allowed to invent a name/id), re-checked against
   those lists before being trusted, same anti-hallucination guard
   node_resolver.py's own `_llm_match` uses.
3. `session_memory["last_network_entity"]`, re-validated against the
   *current* `known_entities` (a network/subnet/instance deleted since an
   earlier turn should not still resolve) -- what lets a bare follow-up
   like "is it still down?" resolve against "the network/subnet/instance
   we were just talking about".

nodes/network.py's fallback order: try `resolve_node` first (a physical-
node question is the common case and the cheapest to match), and only if
that returns nothing, try this module's `resolve_network_entity` -- so a
plain "how's the network on compute-02" question never pays for the extra
OpenStack list calls this resolver's candidate list needs (see
network_health.py's `list_known_networks`/`list_known_subnets`/
`list_known_instances`).
"""
import logging
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from ..services.llm_client import LLMConfigError, get_chat_model

logger = logging.getLogger(__name__)

# Below this, a name is too short/common to trust as a free-text substring
# match on its own (e.g. a stray "net" shouldn't pin a match) -- the LLM
# tier still gets a shot at anything this skips.
_MIN_NAME_LEN = 3


class KnownNetworkEntity(TypedDict):
    kind: Literal["network", "subnet", "instance"]
    id: str
    name: str | None
    cidr: str | None  # only ever set for kind == "subnet"


class _EntityResolution(BaseModel):
    kind: Literal["network", "subnet", "instance", "none"] = Field(
        description=(
            "Which kind of entity the question refers to. 'none' if it doesn't clearly refer "
            "to exactly one of the listed candidates."
        )
    )
    identifier: str | None = Field(
        description=(
            "The exact name (or id, if the entity has no name) of the matched entity, copied "
            "verbatim from the list for that kind -- even if the question spelled it "
            "differently. Null if kind is 'none'."
        )
    )


def _dedupe(entities: list[KnownNetworkEntity]) -> list[KnownNetworkEntity]:
    seen: set[tuple[str, str]] = set()
    unique: list[KnownNetworkEntity] = []
    for e in entities:
        key = (e["kind"], e["id"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _exact_match(query: str, known_entities: list[KnownNetworkEntity]) -> KnownNetworkEntity | None:
    query_lower = query.lower()
    matches: list[KnownNetworkEntity] = []
    for e in known_entities:
        name = (e.get("name") or "").lower()
        if name and len(name) >= _MIN_NAME_LEN and name in query_lower:
            matches.append(e)
            continue
        if e["id"] and e["id"].lower() in query_lower:
            matches.append(e)
            continue
        cidr = e.get("cidr")
        if cidr and cidr in query:
            matches.append(e)

    unique_matches = _dedupe(matches)
    if len(unique_matches) == 1:
        return unique_matches[0]
    return None


def _label(e: KnownNetworkEntity) -> str:
    return e.get("name") or e["id"]


def _candidate_listing(known_entities: list[KnownNetworkEntity]) -> str:
    networks = [e for e in known_entities if e["kind"] == "network"]
    subnets = [e for e in known_entities if e["kind"] == "subnet"]
    instances = [e for e in known_entities if e["kind"] == "instance"]

    lines = []
    if networks:
        lines.append("Networks: " + ", ".join(_label(e) for e in networks))
    if subnets:
        lines.append(
            "Subnets: " + ", ".join(f"{_label(e)} ({e['cidr']})" if e.get("cidr") else _label(e) for e in subnets)
        )
    if instances:
        lines.append("Instances: " + ", ".join(_label(e) for e in instances))
    return "\n".join(lines)


def _llm_match(query: str, known_entities: list[KnownNetworkEntity]) -> KnownNetworkEntity | None:
    by_kind: dict[str, list[KnownNetworkEntity]] = {"network": [], "subnet": [], "instance": []}
    for e in known_entities:
        by_kind[e["kind"]].append(e)

    try:
        llm = get_chat_model(temperature=0, tier="fast")
        structured = llm.with_structured_output(_EntityResolution)
        result = structured.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "You match a question to one network, subnet, or instance from a fixed "
                        "list, or 'none' if it doesn't clearly name one.\n" + _candidate_listing(known_entities)
                    ),
                },
                {"role": "user", "content": query},
            ]
        )
    except LLMConfigError:
        logger.info("network_resolver: LLM not configured, no fuzzy match attempted")
        return None
    except Exception:
        # Includes structured-output parsing failures -- same "the model
        # itself is unreliable" case node_resolver.py's own _llm_match
        # logs at warning, not info.
        logger.warning("network_resolver: LLM entity resolution call failed", exc_info=True)
        return None

    if result.kind == "none" or not result.identifier:
        logger.info("network_resolver: LLM found no matching entity in query %r", query)
        return None

    for e in by_kind[result.kind]:
        if e["id"] == result.identifier or e.get("name") == result.identifier:
            return e
    # Model returned something outside the given list for that kind --
    # don't trust a hallucinated name/id, treat it the same as "no
    # confident match".
    logger.warning(
        "network_resolver: LLM returned identifier %r (kind %r) not in known list, ignoring",
        result.identifier, result.kind,
    )
    return None


def _session_memory_match(
    known_entities: list[KnownNetworkEntity], session_memory: dict | None
) -> KnownNetworkEntity | None:
    if not session_memory:
        return None
    last = session_memory.get("last_network_entity")
    if not last or not last.get("id") or not last.get("kind"):
        return None
    for e in known_entities:
        if e["kind"] == last["kind"] and e["id"] == last["id"]:
            return e
    # The remembered entity isn't in today's candidate list anymore
    # (deleted/renamed since that earlier turn) -- don't resolve to
    # something that no longer exists.
    return None


def resolve_network_entity(
    query: str,
    known_entities: list[KnownNetworkEntity],
    session_memory: dict | None = None,
) -> KnownNetworkEntity | None:
    if not known_entities:
        return None
    if len(known_entities) == 1:
        # Only one candidate across all three kinds -- no ambiguity
        # possible even if the query never names it explicitly, same
        # shortcut node_resolver.resolve_node takes for a single node.
        return known_entities[0]

    entity = _exact_match(query, known_entities)
    if entity is not None:
        return entity

    entity = _llm_match(query, known_entities)
    if entity is not None:
        return entity

    return _session_memory_match(known_entities, session_memory)
