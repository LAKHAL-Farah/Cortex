"""Classifies a question's intent and picks the target agent to handle it.

v0.1 used a hardcoded keyword table (fine with exactly one agent to route
to). Now that there are four (monitoring / prediction / rag / anomaly),
routing is a real classification problem -- "how do we fix X" and "will X
run out of disk" don't share vocabulary with a fixed keyword list you'd
have to keep extending by hand. This is an LLM call via LangChain's
structured output instead: the model reads the question and picks exactly
one of the agent names, constrained to that enum so it can't return
anything the graph doesn't know how to route.

Falls back to DEFAULT_AGENT only if the LLM isn't configured or the call
itself fails -- a missing NVIDIA_API_KEY should degrade routing quality,
not take the whole graph down.
"""
import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..services.llm_client import LLMConfigError, get_chat_model
from .state import CortexState

logger = logging.getLogger(__name__)

AgentName = Literal["monitoring", "prediction", "rag", "anomaly"]

# Safest, cheapest default: a direct status pull, no forecast math,
# knowledge-base retrieval, or multi-source investigation involved.
DEFAULT_AGENT: AgentName = "monitoring"

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

Pick the single best match. If genuinely ambiguous, default to monitoring."""


class _IntentClassification(BaseModel):
    agent: AgentName = Field(description="Which specialist agent should handle this question.")


def route(state: CortexState) -> CortexState:
    query = state["user_query"]
    target: AgentName = DEFAULT_AGENT

    try:
        llm = get_chat_model(temperature=0)
        structured = llm.with_structured_output(_IntentClassification)
        result = structured.invoke(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ]
        )
        target = result.agent
    except LLMConfigError:
        logger.warning("intent_router: LLM not configured, defaulting to %s", DEFAULT_AGENT)
    except Exception:
        logger.exception("intent_router: LLM classification failed, defaulting to %s", DEFAULT_AGENT)

    state["intent"] = target  # intent label == agent name 1:1, same as v0.1
    state["target_agent"] = target
    return state
