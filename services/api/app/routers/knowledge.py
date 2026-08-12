from __future__ import annotations

from ..services.knowledge.ingestion import (
    ingest_knowledge_documents,
)


import logging
import os

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field

from ..security import require_api_key
from ..services.knowledge.qdrant_store import (
    get_collection_name,
    get_qdrant_client,
    validate_collection,
)
from ..services.knowledge.retriever import (
    RetrievedChunk,
    search_knowledge,
)

from ..services.knowledge.chat_service import (
    KnowledgeChatService,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/knowledge",
    tags=["knowledge"],
)




# ============================================================
# REQUEST MODELS
# ============================================================


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(
        min_length=3,
        max_length=2000,
        description=(
            "Question ou texte à rechercher "
            "dans les runbooks Cortex."
        ),
    )

    limit: int = Field(
        default=3,
        ge=1,
        le=20,
    )

    service: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Filtre facultatif : "
            "cinder, loki, prometheus, "
            "openstack, etc."
        ),
    )

    environment: str | None = Field(
        default="production",
        max_length=100,
    )

    document_type: str | None = Field(
        default="runbook",
        max_length=100,
    )

    language: str | None = Field(
        default="fr",
        max_length=20,
    )

    minimum_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Score minimum facultatif. "
            "0.0 désactive le filtrage."
        ),
    )

class KnowledgeChatRequest(BaseModel):
    question: str = Field(
        min_length=3,
        max_length=2000,
        description=(
            "Question utilisateur envoyée "
            "au Cortex Copilot."
        ),
    )

    service: str | None = Field(
        default=None,
        max_length=100,
    )

    environment: str | None = Field(
        default="production",
        max_length=100,
    )

    document_type: str | None = Field(
        default="runbook",
        max_length=100,
    )

    language: str | None = Field(
        default="fr",
        max_length=20,
    )

    limit: int | None = Field(
        default=None,
        ge=1,
        le=20,
    )

# ============================================================
# RESPONSE MODELS
# ============================================================


class KnowledgeSearchResultOut(BaseModel):
    point_id: str
    score: float

    document_id: str
    title: str
    text: str

    source_path: str
    source_name: str | None = None

    chunk_index: int
    token_count: int

    service: str
    environment: str
    criticality: str
    document_type: str
    language: str

    extension: str | None = None

    document_checksum: str | None = None
    chunk_checksum: str | None = None

    embedding_model: str | None = None
    embedding_dimension: int | None = None


class KnowledgeSearchResponse(BaseModel):
    query: str
    result_count: int
    results: list[KnowledgeSearchResultOut]


class KnowledgeHealthResponse(BaseModel):
    status: str

    collection: str
    collection_status: str
    points_count: int | None

    embedding_model: str
    embedding_dimension: int
    embedding_device: str


class KnowledgeIngestionDocumentOut(BaseModel):
    document_id: str
    status: str
    old_points_deleted: int
    new_points_inserted: int


class KnowledgeIngestionResponse(BaseModel):
    documents_found: int
    chunks_generated: int
    inserted: int
    skipped: int
    updated: int
    legacy_deleted: int
    documents: list[KnowledgeIngestionDocumentOut]



class KnowledgeChatSourceOut(BaseModel):
    point_id: str
    document_id: str
    title: str
    source_path: str
    chunk_index: int
    score: float
    service: str | None = None
    citation: str
    snippet: str


class KnowledgeChatResponse(BaseModel):
    answer: str

    grounded: bool
    llm_called: bool

    top_score: float | None = None
    model: str | None = None

    sources: list[KnowledgeChatSourceOut]


# ============================================================
# SERIALIZATION
# ============================================================


def serialize_result(
    result: RetrievedChunk,
) -> KnowledgeSearchResultOut:
    """
    Transforme un résultat interne du retriever
    en modèle API propre.
    """

    metadata = result.metadata

    return KnowledgeSearchResultOut(
        point_id=result.point_id,
        score=result.score,

        document_id=result.document_id,
        title=result.title,
        text=result.text,

        source_path=result.source_path,
        source_name=metadata.get(
            "source_name"
        ),

        chunk_index=result.chunk_index,
        token_count=int(
            metadata.get(
                "token_count",
                0,
            )
        ),

        service=result.service,

        environment=str(
            metadata.get(
                "environment",
                "",
            )
        ),

        criticality=str(
            metadata.get(
                "criticality",
                "",
            )
        ),

        document_type=str(
            metadata.get(
                "document_type",
                "",
            )
        ),

        language=str(
            metadata.get(
                "language",
                "",
            )
        ),

        extension=metadata.get(
            "extension"
        ),

        document_checksum=metadata.get(
            "document_checksum"
        ),

        chunk_checksum=metadata.get(
            "chunk_checksum"
        ),

        embedding_model=metadata.get(
            "embedding_model"
        ),

        embedding_dimension=metadata.get(
            "embedding_dimension"
        ),
    )


# ============================================================
# HEALTH
# ============================================================


@router.get(
    "/health",
    response_model=KnowledgeHealthResponse,
    dependencies=[
        Depends(require_api_key)
    ],
)
def knowledge_health() -> KnowledgeHealthResponse:
    """
    Vérifie la connexion Qdrant et la compatibilité
    de la collection Knowledge avec Cortex.
    """

    try:
        validate_collection()

        client = get_qdrant_client()

        collection_name = (
            get_collection_name()
        )

        collection_info = (
            client.get_collection(
                collection_name
            )
        )

        return KnowledgeHealthResponse(
            status="ok",

            collection=collection_name,

            collection_status=str(
                collection_info.status
            ),

            points_count=(
                collection_info.points_count
            ),

            embedding_model=os.getenv(
                "EMBEDDING_MODEL",
                "intfloat/multilingual-e5-base",
            ),

            embedding_dimension=int(
                os.getenv(
                    "EMBEDDING_DIMENSION",
                    "768",
                )
            ),

            embedding_device=os.getenv(
                "EMBEDDING_DEVICE",
                "cpu",
            ),
        )

    except Exception as exc:
        logger.exception(
            "Knowledge health check failed"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=(
                "Knowledge service "
                "is unavailable."
            ),
        ) from exc


# ============================================================
# SEMANTIC SEARCH
# ============================================================


@router.post(
    "/search",
    response_model=KnowledgeSearchResponse,
    dependencies=[
        Depends(require_api_key)
    ],
)
def search_knowledge_endpoint(
    payload: KnowledgeSearchRequest,
) -> KnowledgeSearchResponse:
    """
    Recherche sémantique dans les runbooks Cortex.

    Pipeline :

        question
            ↓
        embedding E5
            ↓
        Qdrant
            ↓
        chunks les plus pertinents
    """

    try:
        results = search_knowledge(
            query=payload.query,
            limit=payload.limit,
            service=payload.service,
            environment=payload.environment,
            document_type=payload.document_type,
            language=payload.language,
        )

        # Le seuil reste facultatif.
        #
        # Pour l'instant nous ne fixons PAS
        # un seuil global arbitraire comme 0.84.
        #
        # Il sera calibré plus tard avec des
        # questions positives et négatives.

        if payload.minimum_score > 0:
            results = [
                result
                for result in results
                if (
                    result.score
                    >= payload.minimum_score
                )
            ]

        serialized_results = [
            serialize_result(result)
            for result in results
        ]

        return KnowledgeSearchResponse(
            query=payload.query,
            result_count=len(
                serialized_results
            ),
            results=serialized_results,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Knowledge search failed "
            "for query: %s",
            payload.query,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "Knowledge search service "
                "is unavailable."
            ),
        ) from exc


@router.post(
    "/ingest",
    response_model=KnowledgeIngestionResponse,
    dependencies=[
        Depends(require_api_key)
    ],
)
def ingest_knowledge() -> KnowledgeIngestionResponse:
    """
    Lance l'ingestion Knowledge à la demande.
    """

    try:
        result = ingest_knowledge_documents()

        return KnowledgeIngestionResponse(
            documents_found=result.documents_found,
            chunks_generated=result.chunks_generated,
            inserted=result.inserted,
            skipped=result.skipped,
            updated=result.updated,
            legacy_deleted=result.legacy_deleted,
            documents=[
                KnowledgeIngestionDocumentOut(
                    document_id=item.document_id,
                    status=item.status,
                    old_points_deleted=(
                        item.old_points_deleted
                    ),
                    new_points_inserted=(
                        item.new_points_inserted
                    ),
                )
                for item in result.documents
            ],
        )

    except Exception as exc:
        logger.exception(
            "Knowledge ingestion failed"
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "Knowledge ingestion service "
                "is unavailable."
            ),
        ) from exc

# ============================================================
# GROUNDED CHAT
# ============================================================


@router.post(
    "/chat",
    response_model=KnowledgeChatResponse,
    dependencies=[
        Depends(require_api_key)
    ],
)
def chat_knowledge(
    payload: KnowledgeChatRequest,
) -> KnowledgeChatResponse:
    """
    Chat Q&A grounded dans les runbooks Cortex.

    Pipeline :

        question
            ↓
        embedding E5
            ↓
        Qdrant
            ↓
        relevance gate
            ↓
        contexte RAG
            ↓
        NVIDIA NIM
            ↓
        réponse + citations

    Si aucune information suffisamment pertinente
    n'est présente dans les runbooks, le LLM
    n'est pas appelé.
    """

    try:
        service = KnowledgeChatService()

        result = service.answer(
            question=payload.question,
            service=payload.service,
            environment=payload.environment,
            document_type=payload.document_type,
            language=payload.language,
            limit=payload.limit,
        )

        return KnowledgeChatResponse(
            answer=result["answer"],
            grounded=result["grounded"],
            llm_called=result["llm_called"],
            top_score=result.get(
                "top_score"
            ),
            model=result.get(
                "model"
            ),
            sources=[
                KnowledgeChatSourceOut(
                    point_id=source[
                        "point_id"
                    ],
                    document_id=source[
                        "document_id"
                    ],
                    title=source[
                        "title"
                    ],
                    source_path=source[
                        "source_path"
                    ],
                    chunk_index=source[
                        "chunk_index"
                    ],
                    score=source[
                        "score"
                    ],
                    service=source.get(
                        "service"
                    ),
                    citation=source[
                        "citation"
                    ],
                    snippet=source[
                        "snippet"
                    ],
                )
                for source in result[
                    "sources"
                ]
            ],
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Knowledge chat failed "
            "for question: %s",
            payload.question,
        )

        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY
            ),
            detail=(
                "Knowledge chat service "
                "is unavailable."
            ),
        ) from exc