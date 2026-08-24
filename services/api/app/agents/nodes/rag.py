"""RAG agent -- "how do we fix X" / troubleshooting / explanatory questions
(v0.2). Promotes the existing grounded chat (services/knowledge/chat.py,
the same retrieval + generation POST /api/v1/knowledge/chat uses) into the
graph rather than reimplementing it: same Qdrant retrieval, same LangChain
ChatNVIDIA client, same citation/grounding rules -- just the non-streaming
form (answer_sync) since a graph node returns one complete state update,
not a token stream.
"""
import logging

from ...services.knowledge import chat as knowledge_chat
from ...services.knowledge.embeddings import EmbeddingError
from ..state import CortexState

logger = logging.getLogger(__name__)


def rag_agent(state: CortexState) -> CortexState:
    query = state["user_query"]

    try:
        text, chunks = knowledge_chat.answer_sync(query)
    except EmbeddingError:
        logger.exception("rag_agent: embedding failed")
        state["error"] = "Couldn't reach the embeddings service to search the knowledge base."
        state["agent_result"] = None
        return state
    except knowledge_chat.ChatConfigError as exc:
        state["error"] = str(exc)
        state["agent_result"] = None
        return state
    except Exception:
        logger.exception("rag_agent: retrieval/generation failed")
        state["error"] = "Something went wrong searching the knowledge base."
        state["agent_result"] = None
        return state

    state["agent_result"] = {
        "summary": text,
        # Grounded in retrieved chunks vs. the model's own general
        # knowledge (no matching docs found) -- worth reflecting in
        # confidence even though nothing downstream reads it yet.
        "confidence": 0.9 if chunks else 0.3,
        "raw_data": {
            "sources": [
                {
                    "source_path": c.source_path,
                    "doc_title": c.doc_title,
                    "score": c.score,
                }
                for c in chunks
            ]
        },
    }
    state["error"] = None
    return state
