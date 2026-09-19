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

v0.8: all three calls above run on the fast tier (services/llm_client.py)
-- node/metric resolution are short classifications and the narration is
single-source, same reasoning as monitoring_agent. Metric resolution also
prefers `session_memory["last_metric"]` over the hardcoded DEFAULT_METRIC
when the question itself doesn't name one, and this node writes its own
node+metric resolution back into `state["resolved_entities"]` -- together
these are what let "what about now?" continue forecasting *the same*
metric on *the same* node instead of silently resetting to cpu_percent.
"""
import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ...services.forecast_service import get_forecast
from ...services.llm_client import LLMConfigError, get_chat_model
from ..node_resolver import resolve_node, resolve_nodes
from ..state import CortexState

logger = logging.getLogger(__name__)

MetricName = Literal["cpu_percent", "memory_percent", "disk_percent"]
DEFAULT_METRIC: MetricName = "cpu_percent"

class _MetricClassification(BaseModel):
    metric: MetricName = Field(description="The metric the question is asking to forecast.")


def _resolve_metric(query: str, default: MetricName = DEFAULT_METRIC) -> MetricName:
    """`default` is the fallback used both when the LLM can't run at all
    and as the model's own "if unclear" instruction -- callers pass
    session_memory's last_metric here when there is one, so an ambiguous
    follow-up degrades to "whatever we were just forecasting" instead of
    always resetting to cpu_percent."""
    try:
        llm = get_chat_model(temperature=0, tier="fast")
        structured = llm.with_structured_output(_MetricClassification)
        result = structured.invoke(
            [
                {
                    "role": "system",
                    "content": (
                        "Which metric is this forecast question about: cpu_percent, "
                        f"memory_percent, or disk_percent? If unclear, pick {default}."
                    ),
                },
                {"role": "user", "content": query},
            ]
        )
        return result.metric
    except LLMConfigError:
        return default
    except Exception:
        logger.exception("prediction_agent: metric classification failed, defaulting to %s", default)
        return default


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
        llm = get_chat_model(temperature=0.2, tier="fast")
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


_CONCERN_PERCENT = 90.0
_METRIC_LABEL = {"cpu_percent": "CPU", "memory_percent": "memory", "disk_percent": "disk"}


def _forecast_row(node, metric: str, forecast: dict) -> dict | None:
    points = forecast.get("forecast") or []
    if not points:
        return None
    first, last = points[0], points[-1]
    peak = max(p["predicted"] for p in points)
    return {
        "hostname": node["hostname"],
        "role": node["role"],
        "start": first["predicted"],
        "end": last["predicted"],
        "delta": round(last["predicted"] - first["predicted"], 1),
        "peak": peak,
        "may_breach": max(p.get("upper", p["predicted"]) for p in points) >= _CONCERN_PERCENT,
        "will_breach": peak >= _CONCERN_PERCENT,
    }


def _fleet_forecast_table(rows: list[dict], is_percent: bool) -> str:
    unit = "%" if is_percent else ""
    lines = [
        "| Node | Role | Now | End of horizon | Change | Peak | Risk |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        risk = "will cross 90%" if r["will_breach"] else "may cross 90%" if r["may_breach"] else "ok"
        sign = "+" if r["delta"] > 0 else ""
        lines.append(
            f"| {r['hostname']} | {r['role']} | {r['start']}{unit} | {r['end']}{unit} | "
            f"{sign}{r['delta']} | {r['peak']}{unit} | {risk} |"
        )
    return "\n".join(lines)


def _run_multi(state: CortexState, nodes: list, session_memory: dict) -> CortexState:
    metric = _resolve_metric(state["user_query"], default=session_memory.get("last_metric") or DEFAULT_METRIC)
    rows: list[dict] = []
    missing: list[str] = []
    horizon_days = None
    # Sequential on purpose: get_forecast loads/reuses cached model bundles,
    # and nothing in it is documented as safe to call concurrently.
    for node in nodes:
        try:
            forecast = get_forecast(node["instance"].split(":", 1)[0], metric)
        except Exception:
            logger.exception("prediction_agent: get_forecast failed for %s", node["hostname"])
            forecast = None
        row = _forecast_row(node, metric, forecast) if forecast else None
        if row is None:
            missing.append(node["hostname"])
            continue
        horizon_days = horizon_days or forecast.get("horizon_days")
        rows.append(row)

    if not rows:
        state["error"] = (
            f"Not enough data to forecast {metric.replace('_', ' ')} for any of the {len(nodes)} nodes you asked about."
        )
        state["agent_result"] = None
        return state

    rows.sort(key=lambda r: (not r["will_breach"], not r["may_breach"], -r["peak"]))
    is_percent = metric.endswith("_percent")
    at_risk = [r["hostname"] for r in rows if r["will_breach"] or r["may_breach"]]
    label = _METRIC_LABEL.get(metric, metric.replace("_", " "))
    counts = {"total": len(rows), "at_risk": len(at_risk)}
    if is_percent:
        lead = (
            f"{len(at_risk)} of {len(rows)} nodes are projected to reach {int(_CONCERN_PERCENT)}% "
            f"{label}: {', '.join(at_risk)}."
            if at_risk
            else f"None of the {len(rows)} nodes is projected to reach {int(_CONCERN_PERCENT)}% {label}."
        )
    else:
        lead = f"Forecast for {len(rows)} nodes."
    top = max(rows, key=lambda r: r["end"])
    insights = [f"Highest projected end value: {top['hostname']} ({top['end']}{'%' if is_percent else ''})."]
    if missing:
        insights.append(f"Not enough data to forecast: {', '.join(missing)}.")

    summary = (
        f"### {label[0].upper() + label[1:]} forecast — {len(rows)} nodes\n\n"
        f"{lead}\n\n{_fleet_forecast_table(rows, is_percent)}\n\n"
        f"#### Insights\n" + "\n".join(f"- {i}" for i in insights)
    )
    state["agent_result"] = {
        "summary": summary,
        "confidence": 0.8,
        "raw_data": {
            "scope": "multi",
            "metric": metric,
            "horizon_days": horizon_days,
            "concern_percent": _CONCERN_PERCENT,
            "nodes": rows,
            "missing": missing,
            "at_risk": at_risk,
            "counts": counts,
        },
    }
    state["error"] = None
    resolved = state.setdefault("resolved_entities", {})
    resolved["last_nodes"] = list(nodes)
    resolved["last_metric"] = metric
    resolved["last_agent"] = "prediction"
    return state


def prediction_agent(state: CortexState) -> CortexState:
    known_nodes = state["known_nodes"]
    session_memory = state.get("session_memory") or {}
    multi = resolve_nodes(state["user_query"], known_nodes, session_memory=session_memory)
    if multi:
        return _run_multi(state, multi, session_memory)
    node = resolve_node(state["user_query"], known_nodes, session_memory=session_memory)

    if node is None:
        available = ", ".join(n["hostname"] for n in known_nodes) or "no nodes registered"
        state["error"] = (
            f"I couldn't tell which node you meant. Known nodes: {available}."
        )
        state["agent_result"] = None
        return state

    metric = _resolve_metric(state["user_query"], default=session_memory.get("last_metric") or DEFAULT_METRIC)
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
    state.setdefault("resolved_entities", {})["last_node"] = node
    state["resolved_entities"]["last_metric"] = metric
    state["resolved_entities"]["last_agent"] = "prediction"
    return state
