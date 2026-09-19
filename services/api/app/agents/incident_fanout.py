"""Bookkeeping for the parallel incident investigation (v1.2).

The fan-out itself already existed (graph.py: anomaly_dispatch -> one
`Send` per (node, agent) -> anomaly_arbitrate). What it could not do was
*prove* it ran in parallel, or say *why* one agent's theory beat the others.
This module adds exactly those two things and nothing that changes control
flow:

1. `timed_branch` wraps each Send target (anomaly / network / security
   `*_investigate_one`). It logs START/DONE for every branch with the worker
   thread name, and stamps `finding["timing"]` (monotonic start/end) so the
   join node can measure overlap. Threads and timestamps are *observations*
   of what LangGraph really did, not a second scheduler -- if the branches
   were serialized, `peak_concurrency` would honestly read 1.
2. `summarize_parallelism` turns those stamps into the numbers the logs, the
   trace step and the UI show: peak concurrency, wall-clock vs the sum of the
   branches (what a sequential run would have cost), per-branch offsets.
3. `theory_ledger` explains one host's decision: for every agent that looked
   at it -- signal or not, its own confidence, the corroboration bonus, the
   score arbitration ranked on, and a verdict. It only *reports* the ranking
   anomaly.py's `anomaly_arbitrate` already computed (the caller passes the
   scoring callables in), so the ledger can never drift from the decision.

Nothing here reads a finding's free-text summary: the ledger and the
parallelism block carry agent names, booleans and numbers only, which is what
lets them travel in `raw_data` for every role (see services/security_rbac.py)
without leaking a Security finding's specifics.
"""
import functools
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


def timed_branch(agent: str):
    """Decorator for a Send-target `(payload) -> {"agent_results": [...]}`.

    Applied *outside* `guarded_send`, so a branch that timed out or crashed
    and was degraded to a confidence-0 finding still gets a timing stamp --
    a slow branch is exactly the one you want to see in the timeline.
    """

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        @functools.wraps(fn)
        def wrapped(payload: dict) -> dict:
            host = (payload.get("node") or {}).get("hostname", "unknown")
            thread = threading.current_thread().name
            started_at = datetime.now(timezone.utc).isoformat()
            start = time.perf_counter()
            logger.info("incident fan-out: %s branch START host=%s thread=%s", agent, host, thread)
            try:
                out = fn(payload)
            finally:
                end = time.perf_counter()
                logger.info(
                    "incident fan-out: %s branch DONE  host=%s thread=%s duration_ms=%.0f",
                    agent, host, thread, (end - start) * 1000,
                )
            for finding in (out or {}).get("agent_results") or []:
                finding["timing"] = {
                    "start": start,
                    "end": end,
                    "duration_ms": round((end - start) * 1000, 1),
                    "thread": thread,
                    "started_at": started_at,
                }
            return out

        return wrapped

    return decorator


def summarize_parallelism(findings: list[dict]) -> dict | None:
    """Overlap statistics for the branches of one fan-out, or None when no
    finding carries a timing stamp (hand-built findings in a unit test, or a
    fan-out from before this existed)."""
    timed = [f for f in findings if f.get("timing")]
    if not timed:
        return None

    t0 = min(f["timing"]["start"] for f in timed)
    wall = max(f["timing"]["end"] for f in timed) - t0
    sequential = sum(f["timing"]["end"] - f["timing"]["start"] for f in timed)

    # Sweep line: +1 at every start, -1 at every end; an end sorts before a
    # start at the same instant, so back-to-back branches don't count as
    # overlapping.
    events = sorted(
        [(f["timing"]["start"], 1) for f in timed] + [(f["timing"]["end"], -1) for f in timed],
        key=lambda e: (e[0], e[1]),
    )
    peak = current = 0
    for _t, delta in events:
        current += delta
        peak = max(peak, current)

    branches = [
        {
            "agent": f.get("agent", "anomaly"),
            "hostname": f["hostname"],
            "offset_ms": round((f["timing"]["start"] - t0) * 1000, 1),
            "duration_ms": f["timing"]["duration_ms"],
            "thread": f["timing"]["thread"],
        }
        for f in sorted(timed, key=lambda f: f["timing"]["start"])
    ]
    return {
        "branch_count": len(timed),
        "peak_concurrency": peak,
        "concurrent": peak > 1,
        "threads": len({f["timing"]["thread"] for f in timed}),
        "wall_ms": round(wall * 1000, 1),
        "sequential_ms": round(sequential * 1000, 1),
        "wall_s": round(wall, 1),
        "sequential_s": round(sequential, 1),
        "speedup": round(sequential / wall, 1) if wall > 0 else None,
        "branches": branches,
    }


def log_parallelism(parallelism: dict | None) -> None:
    """The one INFO line that answers 'were the agents actually concurrent?'"""
    if not parallelism:
        return
    logger.info(
        "incident fan-out joined: %d branches on %d threads, peak concurrency %d, "
        "wall %.0f ms vs %.0f ms if sequential (%s)",
        parallelism["branch_count"], parallelism["threads"], parallelism["peak_concurrency"],
        parallelism["wall_ms"], parallelism["sequential_ms"],
        f"{parallelism['speedup']}x" if parallelism["speedup"] else "n/a",
    )


def _is_failed(finding: dict) -> bool:
    # guarded_send's degrade path stamps raw_data["error"] (resilience.py).
    return bool((finding["agent_result"].get("raw_data") or {}).get("error"))


def theory_ledger(
    host_findings: list[dict],
    winner: dict,
    *,
    has_signal: Callable[[dict], bool],
    bonus: Callable[[dict], float],
    score: Callable[[dict], float],
) -> list[dict]:
    """One row per agent that investigated this host, in the order arbitration
    ranked them: a signal always sorts ahead of a confident 'found nothing'
    (see anomaly_arbitrate's `_rank_key`), then by score."""
    rows = []
    for f in sorted(host_findings, key=lambda f: (not has_signal(f), -score(f), f["agent"])):
        signal = has_signal(f)
        confidence = f["agent_result"]["confidence"]
        if f is winner:
            verdict = "winner"
        elif signal:
            verdict = "also_flagged"
        elif _is_failed(f):
            verdict = "failed"
        else:
            verdict = "no_signal"
        rows.append(
            {
                "agent": f["agent"],
                "has_signal": signal,
                "verdict": verdict,
                "confidence": round(confidence, 2),
                "confidence_pct": round(confidence * 100),
                "corroboration_bonus": round(bonus(f), 2) if signal else 0.0,
                "score": round(score(f), 2),
            }
        )
    return rows


def explain_decision(host: str, ledger: list[dict]) -> str:
    """One sentence saying why the winner won. Numbers and agent names only."""
    winner = next(r for r in ledger if r["verdict"] == "winner")
    others = [r["agent"] for r in ledger if r["verdict"] == "also_flagged"]
    quiet = [r["agent"] for r in ledger if r["verdict"] == "no_signal"]
    failed = [r["agent"] for r in ledger if r["verdict"] == "failed"]

    if not winner["has_signal"]:
        text = (
            f"No agent found a signal on {host}; the {winner['agent']} agent's reading "
            f"(confidence {winner['confidence']}) is shown as the closest match."
        )
    elif others:
        text = (
            f"The {winner['agent']} agent has the best-supported theory for {host} "
            f"(confidence {winner['confidence']}); {', '.join(others)} also flagged it, "
            f"adding a {winner['corroboration_bonus']} corroboration bonus (score {winner['score']})."
        )
    else:
        text = (
            f"The {winner['agent']} agent is the only one with a signal on {host} "
            f"(confidence {winner['confidence']})."
        )
    if quiet:
        text += f" No signal from {', '.join(quiet)}."
    if failed:
        text += f" Did not complete: {', '.join(failed)}."
    return text
