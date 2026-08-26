"""Classifies a question's intent and picks the target agent to handle it.

v0.1 used a hardcoded keyword table (fine with exactly one agent to route
to). Now that there are four (monitoring / prediction / rag / anomaly),
routing is a real classification problem -- "how do we fix X" and "will X
run out of disk" don't share vocabulary with a fixed keyword list you'd
have to keep extending by hand. This is an LLM call via LangChain's
structured output instead: the model reads the question and picks exactly
one of the agent names, constrained to that enum so it can't return
anything the graph doesn't know how to route.

Falls back to DEFAULT_AGENT if the LLM isn't configured or the call itself
fails (post-retry, via resilience.get_breaker) -- a missing NVIDIA_API_KEY
or a hung/unreachable NIM endpoint should degrade routing quality, not take
the whole graph down. This deliberately does NOT feed into
CortexState["failures"]/compose.py's degraded-answer note: a routing
fallback still runs a real agent that produces full-confidence evidence of
its own kind, which is a different thing from the *evidence itself* being
degraded (see nodes/anomaly.py) -- conflating the two would print "this
answer is degraded" on a perfectly good monitoring/prediction/rag answer
that just got there via the default route instead of a classified one.

v0.5 (adr-0007) adds a genuine clarification gate on top of that, for the
case where the LLM call itself *works* but is honestly unsure which agent
fits: the classifier also reports its own confidence, and below
CLARIFY_THRESHOLD the router asks the user to disambiguate instead of
silently guessing (previously: "if genuinely ambiguous, default to
monitoring" -- a guess dressed up as an answer). This only fires when the
LLM ran successfully; an LLM failure still degrades to DEFAULT_AGENT as
before -- clarifying requires a working classifier to tell ambiguous from
merely-unavailable.
"""
import logging
import os
from typing import Literal

from pydantic import BaseModel, Field

from ..services.llm_client import LLMConfigError, get_chat_model
from .resilience import get_breaker
from .state import CortexState

logger = logging.getLogger(__name__)

AgentName = Literal["monitoring", "prediction", "rag", "anomaly"]

# Safest, cheapest default: a direct status pull, no forecast math,
# knowledge-base retrieval, or multi-source investigation involved.
DEFAULT_AGENT: AgentName = "monitoring"

# Below this, the router asks instead of guessing (see module docstring).
# Env-overridable since "how cautious should routing be" is a product/tuning
# knob, not a code change -- lower it to clarify less often, raise it to
# clarify more.
CLARIFY_THRESHOLD = float(os.environ.get("ROUTER_CLARIFY_THRESHOLD", "0.5"))

_CLARIFYING_QUESTION = (
    "I'm not confident which of these you're asking about -- could you clarify? "
    "I can check current live status (CPU/RAM/disk/uptime/health), forecast a future trend "
    "(e.g. \"will X run out of disk\"), look up how-to/troubleshooting guidance from the "
    "knowledge base, or investigate a suspected incident by correlating metrics and logs."
)

_SYSTEM_PROMPT = """You route a user's infrastructure question to exactly one specialist agent:

- monitoring: current/live status right now -- CPU, RAM, disk, uptime, up/down, health.
- prediction: forecast / future-trend questions -- "will X run out of disk", "CPU trend for \
the next week", "when will Y hit 90%".
- rag: how-to / troubleshooting / explanatory questions -- "how do we fix X", "why does Y \
happen", "what's the procedure for Z", anything about docs, runbooks, or how a system works.
- anomaly: something is wrong / investigate an incident -- "something's wrong with compute-01", \
"why is X acting up", "investigate this alert", "is X having an issue" -- questions that need \
correlating metric and log evidence to figure out what's actually happening, as opposed to a \
plain current-value read (that's monitoring).

Pick the single best match, and honestly report your confidence in that pick from 0.0 (a pure \
guess -- the question could just as easily fit a different agent) to 1.0 (unambiguous). Do not \
inflate confidence to avoid an ambiguous-sounding score -- a low, honest score is exactly what \
lets the system ask a clarifying question instead of guessing wrong."""


class _IntentClassification(BaseModel):
    agent: AgentName = Field(description="Which specialist agent should handle this question.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this routing choice: 0.0 (pure guess) to 1.0 (unambiguous).",
    )


def route(state: CortexState) -> CortexState:
    query = state["user_query"]
    target: AgentName = DEFAULT_AGENT

    try:
        llm = get_chat_model(temperature=0)
        structured = llm.with_structured_output(_IntentClassification)
    except LLMConfigError:
        logger.warning("intent_router: LLM not configured, defaulting to %s", DEFAULT_AGENT)
        state["intent"] = target
        state["target_agent"] = target
        return state

    breaker = get_breaker("router.intent_llm", timeout_seconds=6.0, max_retries=1)
    call_result = breaker.call(
        structured.invoke,
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
    )

    if not call_result.ok:
        logger.warning(
            "intent_router: LLM classification failed (%s), defaulting to %s",
            call_result.failure,
            DEFAULT_AGENT,
        )
        state["intent"] = target
        state["target_agent"] = target
        return state

    classification = call_result.value
    if classification.confidence < CLARIFY_THRESHOLD:
        logger.info(
            "intent_router: confidence %.2f for %r below threshold %.2f, asking for clarification",
            classification.confidence,
            classification.agent,
            CLARIFY_THRESHOLD,
        )
        state["intent"] = "clarify"
        state["target_agent"] = "clarify"
        state["error"] = _CLARIFYING_QUESTION
        state["agent_result"] = None
        return state

    target = classification.agent
    state["intent"] = target  # intent label == agent name 1:1, same as v0.1
    state["target_agent"] = target
    return state
