"""Circuit-breaker wrapper shared by every agent-call site (v0.5, "the
system fails safely").

Before this, each node caught its own *known* failure modes inline (see
e.g. monitoring_agent's `except Exception` around collect_metrics(), or
anomaly_agent's `_check_logs` around loki_client.query_range) and
degraded to a hand-written error string. That works for a call that
raises promptly, but it does nothing for a call that *hangs* -- a stalled
socket read, a chat model that never returns -- and it gives compose.py
no structured signal to tell "this agent's evidence is thinner because
something failed" apart from "this agent's evidence is thinner because
that's just what it found".

This module is the fix for both gaps, applied uniformly rather than
per-node:

1. **A hard wall-clock timeout on every wrapped call**, enforced via a
   thread pool rather than trusting the callee to time out on its own --
   `requests` calls already pass a `timeout=`, but a LangChain chat model
   invocation has no such guarantee, and "the callee promises to time out"
   is exactly the kind of promise this layer exists to not depend on.
2. **One retry** before giving up, since a single transient blip
   (a dropped connection, a momentary DNS hiccup) is common enough to be
   worth one immediate re-attempt rather than failing the whole turn on it.
3. **A `FailureRecord` on final failure** instead of a raised exception --
   every call site gets back a `CallResult` it can branch on, and can
   append the `FailureRecord` to `CortexState["failures"]` so compose.py's
   aggregation step can label the answer as degraded honestly, rather than
   either crashing or silently presenting a thinner finding as if nothing
   went wrong.
4. **Per-call-site circuit state** (closed / open / half-open) so a
   dependency that's confirmed dead doesn't get hammered on every single
   question -- after `failure_threshold` consecutive failures the breaker
   opens and short-circuits (fails fast, no real call attempted) until
   `reset_after_seconds` have passed, then allows one half-open trial.

Deliberately NOT a general resilience library: no bulkheads, no metrics
export, no jittered backoff. Just enough that one flaky dependency
degrades one finding instead of hanging or crashing the chat turn --
which is the actual gap v0.4 shipped with.

v0.7 (adr-0009) adds one more small job to `guarded_node`: since it
already measures wall time and knows ok-vs-failed for every wrapped node,
it also appends the resulting `trace.TraceEvent` to `state["trace_events"]`
-- the one thing per-node tracing needs that only this wrapper already
has both halves of (duration + outcome) without re-deriving them.
"""
import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Generic, Optional, TypedDict, TypeVar

from .trace import record_step

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FailureRecord(TypedDict):
    """One failed (post-retry) call, in a shape compose.py and the API
    response can both surface without needing to know which breaker or
    dependency produced it."""

    source: str  # breaker name, e.g. "anomaly.loki", "monitoring", "router.intent_llm"
    error_type: str  # "timeout" | "circuit_open" | the raised exception's class name
    message: str
    attempts: int
    timestamp: str  # ISO 8601 UTC


def _failure_record(source: str, error_type: str, message: str, attempts: int) -> FailureRecord:
    return {
        "source": source,
        "error_type": error_type,
        "message": message,
        "attempts": attempts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@dataclass
class CallResult(Generic[T]):
    ok: bool
    value: Optional[T] = None
    failure: Optional[FailureRecord] = None


class _CircuitOpenError(RuntimeError):
    """Internal signal only -- call() catches this itself and turns it into
    a CallResult(ok=False, ...). Callers never see this exception type."""


# Guarded calls are I/O-bound (HTTP, DB, an LLM invocation) and expected to
# be brief -- a modest shared pool covers concurrent chat turns without
# spinning up a thread per call. A timed-out call's thread is never killed
# (Python has no public API to interrupt a running thread); it's simply
# abandoned and its eventual result discarded, which is why call sites that
# wrap a *node* (see guarded_node below) run against a copy of state rather
# than the live dict -- a straggler can't race the fallback path.
_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="breaker")


class CircuitBreaker:
    """One breaker per named call site. Held in the module-level registry
    (get_breaker) so the same instance -- and therefore its open/closed
    state -- persists across requests within the process; a breaker that
    reset on every call could never actually open.
    """

    def __init__(
        self,
        name: str,
        timeout_seconds: float = 8.0,
        max_retries: int = 1,
        failure_threshold: int = 3,
        reset_after_seconds: float = 30.0,
    ):
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self.reset_after_seconds = reset_after_seconds

        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._state = "closed"  # closed | open | half_open
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _check_gate(self) -> None:
        with self._lock:
            if self._state != "open":
                return
            assert self._opened_at is not None
            if time.monotonic() - self._opened_at >= self.reset_after_seconds:
                self._state = "half_open"
                return
            raise _CircuitOpenError(f"{self.name}: circuit open, skipping call")

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._state = "closed"
            self._opened_at = None

    def _record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == "half_open" or self._consecutive_failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()

    def call(self, fn: Callable[..., T], *args, **kwargs) -> CallResult[T]:
        try:
            self._check_gate()
        except _CircuitOpenError as exc:
            logger.warning("%s: circuit open, short-circuiting", self.name)
            return CallResult(ok=False, failure=_failure_record(self.name, "circuit_open", str(exc), attempts=0))

        attempts = 0
        error_type = "unknown"
        message = ""
        for _ in range(1 + self.max_retries):
            attempts += 1
            try:
                future = _EXECUTOR.submit(fn, *args, **kwargs)
                value = future.result(timeout=self.timeout_seconds)
                self._record_success()
                return CallResult(ok=True, value=value)
            except FutureTimeoutError:
                error_type = "timeout"
                message = f"{self.name}: call exceeded {self.timeout_seconds}s budget"
                logger.warning("%s: attempt %d timed out after %.1fs", self.name, attempts, self.timeout_seconds)
            except Exception as exc:  # noqa: BLE001 -- deliberately broad: any
                # exception from an arbitrary wrapped call is a failure this
                # layer exists to catch and record, not let propagate.
                error_type, message = type(exc).__name__, str(exc)
                logger.warning("%s: attempt %d failed: %s", self.name, attempts, exc)

        self._record_failure()
        return CallResult(ok=False, failure=_failure_record(self.name, error_type, message, attempts=attempts))


_REGISTRY: dict[str, CircuitBreaker] = {}
_REGISTRY_LOCK = threading.Lock()


def get_breaker(name: str, **overrides) -> CircuitBreaker:
    """Returns the shared breaker for `name`, creating it on first use.
    `overrides` (timeout_seconds, max_retries, failure_threshold,
    reset_after_seconds) only take effect the first time a given name is
    requested -- later calls just get the existing instance, same as
    logging.getLogger(name)."""
    with _REGISTRY_LOCK:
        if name not in _REGISTRY:
            _REGISTRY[name] = CircuitBreaker(name, **overrides)
        return _REGISTRY[name]


def reset_all_breakers() -> None:
    """Test-only escape hatch: breaker state is process-global by design
    (that's what lets a circuit actually open), which means tests that
    exercise failure_threshold/reset_after_seconds need a way to start
    clean instead of inheriting state left by a previous test."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def guarded_send(name: str, timeout_seconds: float = 20.0):
    """Decorator for a LangGraph `Send`-target node function
    `(payload) -> dict`, the fan-out counterpart to guarded_node below
    (v0.8, dynamic incident fan-out -- see agents/nodes/anomaly.py's
    anomaly_investigate_one and graph.py's Send("anomaly_investigate", ...)
    wiring).

    guarded_node's contract (mutate a copy of the *whole* state, return the
    whole thing) only works for a single sequential node -- a Send target
    is invoked once per fan-out branch, concurrently, each against a
    narrow `payload` (not the graph's full state), and must return only a
    partial update for whichever reducer-backed field it's contributing to
    (see state.py's `agent_results`). Returning anything else -- in
    particular, the caller's own already-read copy of that reducer field --
    would get concatenated onto the channel a second time once LangGraph
    merges concurrent branches, silently duplicating every finding.

    On timeout/exception this still can't leave the turn hanging or crash
    the whole fan-out over one unreachable node: instead of state["error"]
    (guarded_node's move, which has no meaning here -- there is no single
    shared "the turn failed") this returns one degraded IncidentFinding
    for `payload["node"]` -- confidence 0.0, the failure explained in its
    own summary -- so one bad node shows up as one bad row in the incident,
    not a lost turn.
    """
    breaker = get_breaker(name, timeout_seconds=timeout_seconds)

    def decorator(fn):
        @functools.wraps(fn)
        def wrapped(payload):
            result = breaker.call(fn, payload)
            if result.ok:
                return result.value

            hostname = (payload.get("node") or {}).get("hostname", "unknown")
            logger.warning(
                "%s: Send-target call failed for %s, degrading that node's finding: %s",
                name, hostname, result.failure,
            )
            return {
                "agent_results": [{
                    "hostname": hostname,
                    "agent_result": {
                        "summary": (
                            f"Couldn't investigate {hostname} in time "
                            f"({result.failure['error_type']}); skipped for this incident."
                        ),
                        "confidence": 0.0,
                        "raw_data": {"hostname": hostname, "error": result.failure},
                    },
                    "failures": [result.failure],
                }],
            }

        return wrapped

    return decorator


def guarded_node(name: str, timeout_seconds: float = 20.0):
    """Decorator for a LangGraph node function `(state) -> state`.

    Every node already handles its own *known* failure modes inline (an
    unresolvable hostname, an unreachable Prometheus) and reports them via
    state["error"], same as before this layer existed -- this decorator is
    the outer safety net for whatever isn't one of those: an unexpected
    exception, or a call that hangs instead of raising (an LLM invocation
    with no server-side timeout is the realistic case).

    Runs the node against a **copy** of the input state. If the call times
    out, its thread is not killed (see _EXECUTOR's docstring) and may still
    be mutating that copy after this function has already moved on to the
    fallback branch -- using a copy means that straggler can never write
    into the state this function (or the graph) actually returns.

    Merges `result.value` back onto `state` via clear-then-update rather
    than a plain `state.update(...)`, so a node that removes a key (rather
    than only adding/overwriting one) actually removes it from the state
    LangGraph sees too -- a plain `.update()` can't express a deletion.
    Every node here already returns the complete state it was given
    (mutated in place), so this behaves identically to the old `.update()`
    in every case that already existed; it only matters for a node that
    deliberately drops a key.
    """
    breaker = get_breaker(name, timeout_seconds=timeout_seconds)

    def decorator(node_fn):
        @functools.wraps(node_fn)
        def wrapped(state):
            started = time.monotonic()
            result = breaker.call(node_fn, dict(state))
            duration_ms = (time.monotonic() - started) * 1000

            if result.ok:
                state.clear()
                state.update(result.value)
                record_step(state, name, "ok", duration_ms)
                return state

            logger.warning("%s: node call failed, degrading this turn: %s", name, result.failure)
            state.setdefault("failures", []).append(result.failure)
            state["agent_result"] = None
            state["error"] = (
                f"The {name} agent didn't respond in time and had to be skipped "
                f"({result.failure['error_type']}). Please try again in a moment."
            )
            record_step(state, name, "error", duration_ms)
            return state

        return wrapped

    return decorator
