"""Single place that knows how to build the LangChain chat model Cortex's
LLM-touching code shares -- the agentic layer's router/agents (app/agents/)
and the knowledge chat endpoint (services/knowledge/chat.py) all go through
this instead of each constructing their own ChatNVIDIA and re-reading the
same env vars, so there's exactly one model/one config to change.

v0.8 (efficiency & scale prep) splits that one model into two *tiers*,
picked per call site rather than per deployment:

- **"fast"**: routing and simple, single-source summarization (intent_router,
  node_resolver's fuzzy match, monitoring/prediction's narration and metric
  classification). These are short, low-stakes calls made on *every* turn --
  as agent count and conversation volume grow, running them on the same
  120B-class model as everything else is the first place cost/latency
  creeps in for no quality benefit a cheaper model wouldn't also clear.
  Defaults to a small hosted NIM model; NVIDIA_NIM_FAST_BASE_URL lets this
  point at a self-hosted/local NIM container instead (the "local" half of
  "fast/local"), completely independent of the reasoning tier's endpoint.
- **"reasoning"**: multi-source synthesis and anything whose output other
  code (or a person) has to actually trust -- RAG generation (adr-0005),
  anomaly's per-node narration and its cross-node incident arbitration
  (nodes/anomaly.py), and reserved for critic/compose the day either of
  those starts making its own LLM call instead of the rule-based checks
  they use today. This keeps the exact model/config every call site used
  before tiering existed.

Same NVIDIA NIM setup this always used (adr-0005): NVIDIA_API_KEY is
required for both tiers, NVIDIA_NIM_MODEL/NVIDIA_NIM_BASE_URL are optional
overrides for the reasoning tier specifically (unchanged env var names, so
an existing deployment's config keeps meaning exactly what it always did).

Incident note (2026-09-04): NVIDIA NIM had a rough week on this key --
nemotron-3-nano-30b-a3b and llama-3.3-70b-instruct hit end-of-life (410),
qwen2.5-72b-instruct wasn't resolvable (404), nemotron-3-ultra-550b-a55b
was overloaded (503), and even nemotron-3-super-120b-a12b -- otherwise the
most reliable model on this key -- returned a bare [500] Internal Server
Error after a 49s hang under real traffic. That last one in particular
means the *model choice* isn't the whole story here: this looks like
capacity/incident issues on NVIDIA's side, not just "pick a better model."
Before spending more time swapping models, check NVIDIA's status page /
your build.nvidia.com account for an active incident, and re-run
scripts/check_nvidia_models.py -- more than once, not just on a quiet
moment -- to see whether this has resolved.
"""
import os
from typing import Literal

from langchain_nvidia_ai_endpoints import ChatNVIDIA

ModelTier = Literal["fast", "reasoning"]

# -- Reasoning tier (unchanged names/defaults from pre-v0.8) --------------
NVIDIA_NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
# ChatNVIDIA defaults to NVIDIA's hosted NIM endpoint (integrate.api.nvidia.com)
# when base_url is omitted -- only set NVIDIA_NIM_BASE_URL if pointing at a
# self-hosted NIM container instead.
NVIDIA_NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL") or None

# -- Fast tier (v0.8) -------------------------------------------------------
# A small, cheap hosted model by default -- swap for a self-hosted/local NIM
# container by setting NVIDIA_NIM_FAST_BASE_URL, same knob shape as the
# reasoning tier's own NVIDIA_NIM_BASE_URL, kept independent so the two
# tiers can live on entirely different infra.
#
# meta/llama-3.1-8b-instruct (the original default here) reached NVIDIA NIM
# end-of-life and 410'd on every call. mistralai/mistral-nemotron (tried
# next, 2026-09-04) answers chat fine but is NOT reliable for
# .with_structured_output() -- langchain_nvidia_ai_endpoints warns it's
# "not known to support structured output", and in practice it intermittently
# returns malformed/null structured results, which silently broke both
# intent_router's classification (wrong agent picked) and node_resolver's
# fuzzy hostname match (falls through to "I couldn't tell which node you
# meant") -- worse than a hard failure since it looks like it's working most
# of the time. Falling back to the reasoning-tier model here trades away the
# fast tier's cost/latency benefit, but every fast-tier call site
# (intent_router, node_resolver, monitoring) needs structured output or a
# short, must-be-correct answer, so correctness comes first. Re-split once a
# small model is confirmed *consistently* clean on structured output --
# don't trust a single passing run of scripts/check_nvidia_models.py for
# this again; run it multiple times / on real traffic.
NVIDIA_NIM_FAST_MODEL = os.environ.get("NVIDIA_NIM_FAST_MODEL", "nvidia/nemotron-3-super-120b-a12b")
NVIDIA_NIM_FAST_BASE_URL = os.environ.get("NVIDIA_NIM_FAST_BASE_URL") or None
# Falls back to NVIDIA_API_KEY -- only set this separately if the fast
# tier's endpoint (e.g. a local/self-hosted NIM container) uses its own
# key, or no auth at all (some local NIM/vLLM setups accept any string).
NVIDIA_NIM_FAST_API_KEY = os.environ.get("NVIDIA_NIM_FAST_API_KEY") or None

_TIER_MODEL = {"fast": NVIDIA_NIM_FAST_MODEL, "reasoning": NVIDIA_NIM_MODEL}
_TIER_BASE_URL = {"fast": NVIDIA_NIM_FAST_BASE_URL, "reasoning": NVIDIA_NIM_BASE_URL}

# Which tier each graph node/agent actually calls an LLM on, if any -- a
# static map (not derived from live call data) since the assignment is a
# deliberate per-call-site choice, not something that varies turn to turn.
# Used by crud.agent_trace_stats to attach a `model_tier` to the 6.3
# cost/latency rollup's per-agent breakdown, and a `router_tier` alongside
# it (the router itself isn't a `target_agent` value, so it wouldn't
# otherwise show up in that breakdown at all).
ROUTER_TIER: ModelTier = "fast"
AGENT_TIERS: dict[str, str] = {
    "monitoring": "fast",
    "prediction": "fast",
    "rag": "reasoning",
    "anomaly": "reasoning",
    # Deterministic catalog matching (openstack_expert_catalog.py) -- no
    # LLM call at all, so neither tier applies.
    "openstack_expert": "n/a (no LLM call)",
    # No agent ran -- the router asked the user to disambiguate instead.
    "clarify": "n/a (no agent ran)",
}


class LLMConfigError(RuntimeError):
    """Raised when NVIDIA_API_KEY is missing. Every LLM-touching call site
    in the agentic layer catches this specifically and degrades gracefully
    (a cheaper/dumber fallback, never a crash) -- an agent's job is to
    answer the question, not to insist an LLM is reachable."""


def require_configured() -> None:
    if not os.environ.get("NVIDIA_API_KEY"):
        raise LLMConfigError(
            "NVIDIA_API_KEY is not set -- required to call the NVIDIA NIM chat endpoint"
        )


def get_chat_model(
    temperature: float = 0.2,
    max_tokens: int | None = None,
    tier: ModelTier = "reasoning",
) -> ChatNVIDIA:
    """Returns a fresh ChatNVIDIA instance for the given tier. Callers doing
    structured output (`.with_structured_output(...)`) should pass
    temperature=0 -- a classification/extraction call has one right answer,
    not something to sample creatively from.

    `tier` defaults to "reasoning" (the old, single-tier behavior) so any
    call site that doesn't pass one explicitly keeps its prior quality
    rather than silently downgrading -- every call site in this codebase
    passes `tier` explicitly (see module docstring for which is which).
    """
    require_configured()
    api_key = NVIDIA_NIM_FAST_API_KEY if (tier == "fast" and NVIDIA_NIM_FAST_API_KEY) else os.environ["NVIDIA_API_KEY"]
    kwargs = {
        "model": _TIER_MODEL[tier],
        "api_key": api_key,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    base_url = _TIER_BASE_URL[tier]
    if base_url:
        kwargs["base_url"] = base_url
    return ChatNVIDIA(**kwargs)
