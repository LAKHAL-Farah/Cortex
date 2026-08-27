"""
services/api/scripts/eval_router_golden_set.py

v0.7 (adr-0009) DoD: "CI fails if a router prompt change drops routing
accuracy below the golden-set baseline." Run this against the real
NVIDIA NIM classifier intent_router.route() actually calls -- there's no
meaningful way to gate a *prompt* change without exercising the real
model that prompt is sent to (mocking the classifier's response, the way
test_intent_router.py's unit tests do, would just be asserting the mock
agrees with itself).

Usage:
    # normal CI gate: fails (exit 1) if current accuracy < stored baseline
    python3 scripts/eval_router_golden_set.py

    # after a deliberate, reviewed routing improvement, record the new
    # baseline (never do this to silence a regression -- only when the
    # accuracy genuinely went up and that's expected)
    python3 scripts/eval_router_golden_set.py --write-baseline

Requires NVIDIA_API_KEY (same as the router itself, see
app/services/llm_client.py). Skips cleanly (exit 0, warning printed) when
it isn't set -- intent_router.route() itself falls back silently to
DEFAULT_AGENT without one, which would make every non-monitoring golden
question "fail" for a reason that has nothing to do with the router
prompt, so this script checks for the key itself up front rather than
letting that fallback produce a meaningless accuracy number.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.intent_router import route  # noqa: E402

_GOLDEN_SET_PATH = Path(__file__).parent.parent / "tests" / "golden" / "routing_golden_set.json"
_BASELINE_PATH = Path(__file__).parent.parent / "tests" / "golden" / "routing_baseline.json"

# A prompt change that drops accuracy by more than one question out of the
# golden set is a real regression; day-to-day model nondeterminism on one
# borderline question (there are several deliberately-ambiguous "clarify"
# cases in the golden set, see routing_golden_set.json's r37-r40) shouldn't
# fail the build on its own.
_TOLERANCE = 1.0 / 40


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def run_eval() -> tuple[float, list[dict]]:
    golden = _load_json(_GOLDEN_SET_PATH)
    results = []
    for item in golden["questions"]:
        state = {"user_query": item["query"], "known_nodes": [], "failures": []}
        routed = route(state)
        actual = routed["target_agent"]
        passed = actual == item["expected_target_agent"]
        results.append({**item, "actual_target_agent": actual, "passed": passed})

    accuracy = sum(r["passed"] for r in results) / len(results)
    return accuracy, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Record the current accuracy as the new baseline instead of gating against it.",
    )
    args = parser.parse_args()

    if not os.environ.get("NVIDIA_API_KEY"):
        print(
            "WARNING: NVIDIA_API_KEY not set -- skipping the routing golden-set eval. "
            "This does NOT gate the build; set the secret in CI for this check to be meaningful.",
            file=sys.stderr,
        )
        return 0

    accuracy, results = run_eval()
    failures = [r for r in results if not r["passed"]]

    print(f"Routing golden set: {accuracy:.1%} ({len(results) - len(failures)}/{len(results)})")
    for r in failures:
        print(
            f"  FAIL [{r['id']}] {r['query']!r}: "
            f"expected {r['expected_target_agent']!r}, got {r['actual_target_agent']!r}"
        )

    if args.write_baseline:
        _BASELINE_PATH.write_text(
            json.dumps(
                {
                    "accuracy": accuracy,
                    "question_count": len(results),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Wrote new baseline: {accuracy:.1%} -> {_BASELINE_PATH}")
        return 0

    if not _BASELINE_PATH.exists():
        print(
            f"No baseline recorded yet at {_BASELINE_PATH}. "
            "Run with --write-baseline once to establish one.",
            file=sys.stderr,
        )
        return 1

    baseline = _load_json(_BASELINE_PATH)["accuracy"]
    print(f"Baseline: {baseline:.1%} (tolerance: {_TOLERANCE:.1%})")

    if accuracy < baseline - _TOLERANCE:
        print(
            f"REGRESSION: accuracy {accuracy:.1%} is below baseline {baseline:.1%} "
            f"(tolerance {_TOLERANCE:.1%}). A router prompt change likely caused this.",
            file=sys.stderr,
        )
        return 1

    print("OK: no regression against baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
