"""Structured run tracing (v0.7, adr-0009).

Every orchestrator run gets a `trace_id` (routers/agents.py mints it before
`app_graph.invoke(...)`, see that module's docstring) and a `trace_events`
list that each node appends one entry to as it runs. This is deliberately
built as *state*, not a side-channel logger or a DB write from inside a
node: `state.py`'s existing contract is that "the graph itself never
touches the DB session" and "state must stay JSON-serializable" (see that
module's `KnownNode` docstring) -- a trace event is just another
JSON-serializable field riding along in the same dict every node already
reads and returns, and routers/agents.py (which already owns the DB
session for this request) is the one place that persists the finished
list to Postgres, once, after the graph completes. This mirrors the
`failures` list resilience.py already threads through state the same way,
just for "what happened", not just "what failed".

`record_step` is the single place a `TraceEvent` gets built, so every
event has the same shape regardless of which node produced it -- this is
what makes "why did it say that" a lookup instead of an investigation
(v0.7's stated goal): one ordered list, one shape, per trace_id.
"""
import functools
import time
from datetime import datetime, timezone
from typing import Callable, Optional, TypedDict


class TraceEvent(TypedDict):
    node: str
    status: str  # "ok" | "error" | "skipped"
    duration_ms: float
    timestamp: str  # ISO 8601 UTC, when the step *finished*
    detail: dict  # small, JSON-safe -- e.g. {"target_agent": "anomaly"}


def _safe_detail(state: dict, node: str) -> dict:
    """A handful of state fields worth having at a glance in the trace
    without dumping the whole (potentially large) state dict -- raw_data
    from an AgentResult can carry full metric series or forecast points,
    which belongs in the DB row's own payload if needed, not repeated
    into every step's detail blob."""
    detail: dict = {}
    if node == "router":
        detail["intent"] = state.get("intent")
        detail["target_agent"] = state.get("target_agent")
    elif node == "critic":
        detail["critic_verdict"] = state.get("critic_verdict")
    else:
        result = state.get("agent_result")
        if result:
            detail["confidence"] = result.get("confidence")
    if state.get("error"):
        detail["error"] = state["error"]
    return detail


def record_step(state: dict, node: str, status: str, duration_ms: float) -> None:
    """Appends one TraceEvent to state["trace_events"] in place. Safe to
    call even when tracing wasn't initialized (no trace_id on state, e.g.
    a unit test invoking a node function directly) -- events just
    accumulate in a list nothing ever reads in that case."""
    state.setdefault("trace_events", []).append(
        TraceEvent(
            node=node,
            status=status,
            duration_ms=round(duration_ms, 2),
            timestamp=datetime.now(timezone.utc).isoformat(),
            detail=_safe_detail(state, node),
        )
    )


def traced(name: str) -> Callable:
    """Decorator for a plain `(state) -> state` graph node (router,
    critic, compose -- the nodes that aren't already wrapped by
    resilience.guarded_node, which records its own trace event directly
    since it already has the try/timeout/failure machinery needed to know
    whether a node succeeded). Records one "ok" event on normal return;
    an exception here still propagates (unlike guarded_node, these nodes
    are simple, synchronous, and don't call out to slow/unreliable
    dependencies, so there's no timeout/degrade case to catch)."""

    def decorator(node_fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        @functools.wraps(node_fn)
        def wrapped(state: dict) -> dict:
            started = time.monotonic()
            result = node_fn(state)
            record_step(result, name, "ok", (time.monotonic() - started) * 1000)
            return result

        return wrapped

    return decorator


def new_trace_id() -> str:
    import uuid

    return uuid.uuid4().hex


def summarize(trace_events: Optional[list[TraceEvent]]) -> dict:
    """Small rollup used by both the /trace/{id} lookup response and
    tests -- total wall time and a per-node failure flag, without a
    caller needing to walk the list itself."""
    events = trace_events or []
    return {
        "step_count": len(events),
        "total_duration_ms": round(sum(e["duration_ms"] for e in events), 2),
        "any_failed": any(e["status"] == "error" for e in events),
    }
