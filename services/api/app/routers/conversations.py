import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..db import get_db
from ..security import get_client_id, require_api_key

router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["conversations"],
    dependencies=[Depends(require_api_key)],
)


def _to_out(conversation, messages) -> schemas.ConversationOut:
    return schemas.ConversationOut(
        id=conversation.id,
        title=conversation.title,
        category=conversation.category,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[schemas.ConversationMessageOut.model_validate(m) for m in messages],
    )


@router.get("", response_model=list[schemas.ConversationSummaryOut])
def list_conversations(client_id: str = Depends(get_client_id), db: Session = Depends(get_db)):
    """Summaries only (no messages) -- this backs the history rail, which
    only ever shows title + last-updated per thread. Fetching full
    transcripts for every conversation just to render a sidebar would scale
    badly once a client_id has more than a handful of threads.
    """
    return crud.list_conversations(db, client_id)


@router.get("/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(
    conversation_id: uuid.UUID, client_id: str = Depends(get_client_id), db: Session = Depends(get_db)
):
    conversation = crud.get_conversation(db, client_id, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    messages = crud.list_conversation_messages(db, conversation.id)
    return _to_out(conversation, messages)


@router.post("", response_model=schemas.ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: schemas.ConversationCreate, client_id: str = Depends(get_client_id), db: Session = Depends(get_db)
):
    conversation = crud.create_conversation(db, client_id, payload)
    return _to_out(conversation, [])


@router.put("/{conversation_id}", response_model=schemas.ConversationOut)
def replace_conversation(
    conversation_id: uuid.UUID,
    payload: schemas.ConversationUpdate,
    client_id: str = Depends(get_client_id),
    db: Session = Depends(get_db),
):
    """Full-replace, matching how the client already treats a conversation
    as one blob (see schemas.ConversationUpdate) -- every turn re-sends the
    complete title/category/messages, not just a delta.
    """
    conversation = crud.get_conversation(db, client_id, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    conversation = crud.replace_conversation(db, conversation, payload)
    messages = crud.list_conversation_messages(db, conversation.id)
    return _to_out(conversation, messages)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: uuid.UUID, client_id: str = Depends(get_client_id), db: Session = Depends(get_db)
):
    conversation = crud.get_conversation(db, client_id, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    crud.delete_conversation(db, conversation)
