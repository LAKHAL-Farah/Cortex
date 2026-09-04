"""Checks which NVIDIA NIM models are actually usable by Cortex's agentic
layer -- meaning plain chat (nodes/anomaly.py, nodes/rag.py's reasoning-tier
narration) AND langchain_nvidia_ai_endpoints' ChatNVIDIA.with_structured_output()
(intent_router / node_resolver / prediction's fast-tier classification).

An earlier version of this script probed raw OpenAI-style tools=[...] +
tool_calls instead of with_structured_output(). That's the wrong probe for
this codebase: with_structured_output() in langchain-nvidia-ai-endpoints
does not go through bind_tools()/tool_calls at all. For a Pydantic schema
it tries, in order (hosted endpoint): OpenAI-compatible
response_format={"type": "json_schema", ...}, then NVIDIA's own
guided_json (direct, then via the nvext param) -- three chains, none of
them the tool-calling code path. A model can fail a raw tool_call probe
and still work perfectly fine here, which is exactly what was happening
with nvidia/nemotron-3-super-120b-a12b. Conversely a model could in
principle emit tool_calls just fine and still fail structured output if
it doesn't support guided_json/json_schema mode -- so the only accurate
check is to call the real method with a realistic schema, which is what
this script does.

Usage (from services/api, same as before):
    docker compose -f docker-compose.yml -f docker-compose.sandbox.yml \\
        exec api python3 scripts/check_nvidia_models.py [--models model1,model2,...]
"""
import argparse
import json
import os
import sys
import time

import requests
from pydantic import BaseModel, Field

NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL") or "https://integrate.api.nvidia.com/v1"

# Candidates worth checking as of this script's writing: the two models
# Cortex actually configures by default (see app/services/llm_client.py),
# plus a few neighbors in case those need to move again later. Widen with
# --models if the catalog has moved on again.
DEFAULT_CANDIDATES = [
    "nvidia/nemotron-3-super-120b-a12b",  # reasoning tier default
    "nvidia/nemotron-3-nano-30b-a3b",     # fast tier default (this patch)
    "nvidia/nemotron-3-ultra-550b-a55b",
    "meta/llama-3.3-70b-instruct",
    "mistralai/mistral-nemotron",
    "qwen/qwen2.5-72b-instruct",
]


class _SmokeTestSchema(BaseModel):
    """Mirrors the *shape* of real call sites (intent_router._IntentClassification,
    node_resolver._NodeResolution, prediction._MetricClassification): a
    small object with an enum-like field and a confidence float, produced
    via .with_structured_output(). Not meant to match any of them exactly,
    just to exercise the same guided_json/json_schema code path they use.
    """

    category: str = Field(description="One of: greeting, question, other.")
    confidence: float = Field(ge=0.0, le=1.0)


def fetch_catalog(api_key: str) -> set[str]:
    resp = requests.get(
        f"{NIM_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()
    return {m["id"] for m in resp.json().get("data", [])}


def check_chat(model: str, api_key: str) -> tuple[str, float | None]:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    llm = ChatNVIDIA(model=model, api_key=api_key, base_url=NIM_BASE_URL, temperature=0)
    start = time.monotonic()
    try:
        llm.invoke("Say OK.")
        return "OK", time.monotonic() - start
    except Exception as e:  # noqa: BLE001 -- deliberately broad, this is a probe script
        return f"FAIL ({e})", None


def check_structured_output(model: str, api_key: str) -> str:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    llm = ChatNVIDIA(model=model, api_key=api_key, base_url=NIM_BASE_URL, temperature=0)
    try:
        structured = llm.with_structured_output(_SmokeTestSchema)
        result = structured.invoke("Classify this message: 'hello there'")
        if result is None:
            return "NO PARSEABLE OUTPUT"
        return "OK"
    except Exception as e:  # noqa: BLE001
        return f"FAIL ({e})"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", help="Comma-separated model ids to check instead of the defaults.")
    args = parser.parse_args()

    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("NVIDIA_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    candidates = args.models.split(",") if args.models else DEFAULT_CANDIDATES

    print(f"NIM endpoint: {NIM_BASE_URL}")
    print("Fetching /v1/models catalog for this key...")
    try:
        catalog = fetch_catalog(api_key)
        print(f"  {len(catalog)} models visible to this key.")
    except Exception as e:  # noqa: BLE001
        print(f"  Could not fetch catalog ({e}); continuing without it.")
        catalog = set()

    rows = []
    for i, model in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {model}")
        in_catalog = "yes" if (not catalog or model in catalog) else "NO"
        chat_status, elapsed = check_chat(model, api_key)
        print(f"  chat: {chat_status}" + (f" [{elapsed:.2f}s]" if elapsed else ""))
        if chat_status == "OK":
            structured_status = check_structured_output(model, api_key)
            print(f"  structured_output: {structured_status}")
        else:
            structured_status = "?"
        rows.append((model, in_catalog, chat_status.split(" ")[0], structured_status.split(" ")[0]))

    print("=" * 78)
    print(f"{'MODEL':<45} {'CATALOG':<9} {'CHAT':<7} {'STRUCTURED_OUTPUT':<10}")
    print("-" * 78)
    for model, in_catalog, chat_status, structured_status in rows:
        print(f"{model:<45} {in_catalog:<9} {chat_status:<7} {structured_status:<10}")
    print("=" * 78)

    passing = [r[0] for r in rows if r[2] == "OK" and r[3] == "OK"]
    if not passing:
        print("No candidate passed both chat + structured_output checks -- widen --models or check rate limits.")
    else:
        print(f"Usable for fast-tier (structured output) call sites: {', '.join(passing)}")

    with open("nvidia_model_check.json", "w") as f:
        json.dump(
            [
                {"model": m, "catalog": c, "chat": ch, "structured_output": s}
                for m, c, ch, s in rows
            ],
            f,
            indent=2,
        )
    print("Full report written to nvidia_model_check.json")


if __name__ == "__main__":
    main()
