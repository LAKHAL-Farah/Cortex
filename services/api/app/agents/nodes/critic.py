"""Critic node (v0.7, adr-0009) -- an evidence-grounding check that runs
after every agent branch and before compose. Answers one narrow question:
does `agent_result["summary"]` claim anything the agent's own
`raw_data`/the user's question doesn't actually support?

**Deliberately not an LLM call.** ADR-0008 already made this exact
argument for the OpenStack Expert Agent's symptom matcher (`_match_symptoms`
over an LLM classifier): a judgment that gates what ships needs to be
exact and auditable, not "plausible-sounding". A critic that itself calls
an LLM to judge another LLM's output has no better guarantee of being
right, and failure modes compound instead of cancel. So this is two fixed,
inspectable checks, unit-testable the same way `_match_symptoms` is:

1. **Numeric grounding.** Every agent that narrates over real numbers
   (monitoring_agent's CPU/RAM/disk, prediction_agent's forecast values,
   anomaly_agent's z-scores/current_value) has those numbers sitting
   verbatim in `raw_data` -- an LLM narrating "94% CPU" when raw_data says
   61.2 is exactly the failure mode a bare metric read has no business
   producing, and it's mechanically checkable: every number the summary
   states must appear (within rounding) somewhere in raw_data or the
   user's own question.
2. **Lexical grounding**, for the one agent whose summary is genuinely
   free-text generation over retrieved documents rather than narration of
   numbers it was handed (`rag_agent`, see nodes/rag.py's `text_snippet`
   addition): a sentence whose content words barely overlap with anything
   in the retrieved chunks, when chunks *were* retrieved, is a sentence
   the model produced from its own general knowledge instead of what was
   actually found -- exactly the "ungrounded claim" this node exists to
   catch. Skipped when there's no evidence pool to check against (no
   chunks retrieved at all) -- that's `rag_agent`'s own 0.3-confidence
   "no context" path, already labeled as such, not this node's job to
   re-flag.

A "flagged" verdict does not discard the answer -- see compose.py, which
adds a caution note and caps confidence, the same "degrade honestly,
don't hide or crash" philosophy resilience.py established for failures.
"""
import re

from ..state import CortexState

# Common short words that carry no topical content -- excluded from the
# lexical overlap check so "the", "and", "with" don't inflate a sentence's
# apparent grounding. Deliberately short and hand-maintained (like
# compose.py's _SOURCE_LABELS) rather than importing an NLP stopword list
# for what's a handful of high-frequency function words.
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "with", "this", "that",
    "have", "from", "your", "will", "can", "about", "into", "than", "then",
    "them", "they", "their", "what", "when", "where", "which", "while",
    "should", "would", "could", "there", "here", "also", "each", "does",
    "doing", "done", "been", "being", "over", "under", "once", "only",
    "just", "more", "most", "some", "such", "same", "these", "those",
    "cortex",
}

# Negative lookbehind excludes digits that are part of an identifier
# rather than a standalone figure -- e.g. "compute-02" or "storage-09"
# (hostnames throughout this codebase, see nodes.py/dashboard.py) would
# otherwise register as the numeric claim "2" or "9" and get flagged
# purely for being mentioned by name.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_-])\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]{3,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# A number this close to something actually in the evidence counts as the
# same claim -- narration is allowed to round ("about 90%" for 89.6), it's
# not allowed to invent a different figure.
_NUMBER_TOLERANCE = 1.0

# Below this fraction of a rag sentence's content words appearing in the
# retrieved chunk text, treat the sentence as ungrounded. Env-overridable
# would be reasonable if this ever needs product tuning the way
# intent_router's CLARIFY_THRESHOLD is, but v1 keeps it a constant --
# there's no usage data yet to tune it against.
_LEXICAL_OVERLAP_THRESHOLD = 0.35
# Sentences shorter than this many content words are too thin to judge
# reliably (a 3-word sentence failing "35% overlap" is one unlucky word,
# not a real signal) -- skipped rather than flagged.
_MIN_CONTENT_WORDS_TO_JUDGE = 4


def _numbers_in(text: str) -> set[float]:
    return {float(m) for m in _NUMBER_RE.findall(text)}


def _walk_numbers(value, out: set[float]) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        out.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _walk_numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk_numbers(v, out)
    elif isinstance(value, str):
        out |= _numbers_in(value)


def _evidence_numbers(raw_data: dict, user_query: str) -> set[float]:
    pool: set[float] = set()
    _walk_numbers(raw_data, pool)
    pool |= _numbers_in(user_query)
    return pool


def _check_numeric_grounding(summary: str, raw_data: dict, user_query: str) -> list[str]:
    evidence = _evidence_numbers(raw_data, user_query)
    if not evidence:
        # Nothing numeric to check against -- an agent whose evidence is
        # entirely non-numeric (e.g. rag's doc chunks) has nothing for
        # this specific check to do; the lexical check covers that case.
        return []

    flagged = []
    for sentence in _split_sentences(summary):
        for number in _numbers_in(sentence):
            if not any(abs(number - e) <= _NUMBER_TOLERANCE for e in evidence):
                flagged.append(sentence.strip())
                break
    return flagged


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s]


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOPWORDS}


def _check_lexical_grounding(summary: str, raw_data: dict) -> list[str]:
    sources = raw_data.get("sources") if isinstance(raw_data, dict) else None
    if not sources:
        return []  # nothing retrieved -- rag_agent's own no-context path, not this node's concern

    evidence_words: set[str] = set()
    for source in sources:
        evidence_words |= _content_words(source.get("text_snippet", ""))
        evidence_words |= _content_words(source.get("doc_title") or "")
    if not evidence_words:
        return []

    flagged = []
    for sentence in _split_sentences(summary):
        words = _content_words(sentence)
        if len(words) < _MIN_CONTENT_WORDS_TO_JUDGE:
            continue
        overlap = len(words & evidence_words) / len(words)
        if overlap < _LEXICAL_OVERLAP_THRESHOLD:
            flagged.append(sentence.strip())
    return flagged


def critic_check(state: CortexState) -> CortexState:
    result = state.get("agent_result")
    if state.get("error") or not result:
        # Nothing to grade -- clarify turns, and any hard error path,
        # never reach here with a summary to check.
        state["critic_verdict"] = {"status": "pass", "checked_sentences": 0, "flagged_claims": []}
        return state

    summary = result.get("summary", "")
    raw_data = result.get("raw_data") or {}
    flagged = _check_numeric_grounding(summary, raw_data, state["user_query"])
    flagged += _check_lexical_grounding(summary, raw_data)
    # Dedupe while preserving order -- the same sentence could in
    # principle fail both checks (unlikely, but no reason to report it
    # twice if it does).
    seen: set[str] = set()
    unique_flagged = []
    for claim in flagged:
        if claim not in seen:
            seen.add(claim)
            unique_flagged.append(claim)

    state["critic_verdict"] = {
        "status": "flagged" if unique_flagged else "pass",
        "checked_sentences": len(_split_sentences(summary)),
        "flagged_claims": unique_flagged,
    }
    return state
