"""Prediction agent -- forecast / future-trend questions (v0.2). Wraps the
existing forecasting service (app/services/forecast_service.py, the same
model GET /api/v1/forecast/{hostname}/{metric} serves) -- no LLM in the
actual forecast math, same reasoning as the monitoring agent: the numbers
are model output already, an LLM narrating on top of them doesn't get to
change what they say.

The LLM is used for:
- Resolving which node the question is about (node_resolver.py -- same
  partial/deformed-hostname handling as monitoring).
- Resolving which metric (cpu_percent / memory_percent / disk_percent) the
  question is asking to forecast.
- Narrating the trajectory in plain language instead of a raw list of
  forecast points. Falls back to a plain templated sentence if the LLM
  isn't configured or the call fails.
"""
import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ...services.forecast_service import get_forecast
from ...services.llm_client import LLMConfigError, get_chat_model
from ..node_resolver import resolve_node
from ..state import CortexState

logger = logging.getLogger(__name__)

MetricName = Literal["cpu_percent", "memory_percent", "disk_percent"]
DEFAULT_METRIC: MetricName = "cpu_percent"

_METRIC_SYSTEM_PROMPT = (
    "Which metric is this forecast question about: cpu_percent, memory_percent, or "
    "disk_percent? If unclear, pick cpu_percent."
)


class _MetricClassification(BaseModel):
    metric: MetricName = Field(description="The metric the question is asking to forecast.")


def _resolve_metric(query: str) -> MetricName:
    try:
        llm = get_chat_model(temperature=0)
        structured = llm.with_structured_output(_MetricClassification)
        result = structured.invoke(
            [
                {"role": "system", "content": _METRIC_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ]
        )
        return result.metric
    except LLMConfigError:
        return DEFAULT_METRIC
    except Exception:
        logger.exception("prediction_agent: metric classification failed, defaulting to %s", DEFAULT_METRIC)
        return DEFAULT_METRIC


_NARRATION_SYSTEM_PROMPT = """You are Cortex's forecasting assistant. Summarize the forecast \
trajectory below for the user in 2-4 sentences: the current trend (rising/falling/flat), where \
it's expected to be at the end of the horizon, and whether it's projected to cross a concerning \
threshold (~90%). Use ONLY the numbers given -- never invent a value."""


def _fallback_summary(node, metric: str, forecast: dict) -> str:
    points = forecast.get("forecast") or []
    if not points:
        return f"No forecast points available for {node['hostname']} / {metric}."
    first, last = points[0], points[-1]
    return (
        f"{node['hostname']}'s {metric.replace('_', ' ')} is projected to go from "
        f"{first['predicted']}% to {last['predicted']}% over the next "
        f"{forecast['horizon_days']} day(s)."
    )


def _narrate(query: str, node, metric: str, forecast: dict) -> str:
    points = forecast.get("forecast") or []
    first = points[0] if points else None
    last = points[-1] if points else None
    try:
        llm = get_chat_model(temperature=0.2)
        response = llm.invoke(
            [
                SystemMessage(content=_NARRATION_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Question: {query}\n\n"
                        f"Node: {node['hostname']}, metric: {metric}\n"
                        f"Model type: {forecast.get('model_type')}\n"
                        f"Horizon: {forecast.get('horizon_days')} day(s)\n"
                        f"First forecast point: {first}\n"
                        f"Last forecast point: {last}"
                    )
                ),
            ]
        )
        text = (response.content or "").strip()
        return text or _fallback_summary(node, metric, forecast)
    except LLMConfigError:
        return _fallback_summary(node, metric, forecast)
    except Exception:
        logger.exception("prediction_agent: LLM narration failed, using fallback summary")
        return _fallback_summary(node, metric, forecast)


def prediction_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    node = resolve_node(state["user_query"], known_nodes)

    if node is None:
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = (
            f"I couldn't tell which node you meant. Known nodes: {available}."
        )
        state["agent_result"] = None
        return state

    metric = _resolve_metric(state["user_query"])
    # get_forecast keys off the identifier forecast_dataset_builder wrote
    # into its dataset -- the host portion of the Prometheus `instance`
    # label, i.e. the IP address, same translation routers/forecast.py does
    # via node.ip_address (KnownNode's "instance" is "{ip}:{port}").
    ip_address = node["instance"].split(":", 1)[0]

    try:
        forecast = get_forecast(ip_address, metric)
    except Exception:
        logger.exception("prediction_agent: get_forecast failed")
        forecast = None

    if forecast is None:
        state["error"] = (
            f"Not enough data to forecast {metric.replace('_', ' ')} for {node['hostname']}."
        )
        state["agent_result"] = None
        return state

    summary = _narrate(state["user_query"], node, metric, forecast)

    state["agent_result"] = {
        "summary": summary,
        "confidence": 0.8,  # model-derived projection, not a direct live reading
        "raw_data": forecast,
    }
    state["error"] = None
    return state
