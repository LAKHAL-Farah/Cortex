import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crud, models, schemas
from ..auth import get_current_user
from ..db import get_db

# No router-level auth dependency here -- every router in main.py already
# gets Depends(get_current_user) applied at app.include_router() time, so
# any route defined below already requires a logged-in account without
# needing to say so again. Routes still take Depends(get_current_user)
# directly (instead of just relying on that router-level guard) because
# they need the actual User object -- specifically user.id, to scope
# conversations to the account rather than just gate access to the route.
router = APIRouter(
    prefix="/api/v1/conversations",
    tags=["conversations"],
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
def list_conversations(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Summaries only (no messages) -- this backs the history rail, which
    only ever shows title + last-updated per thread. Fetching full
    transcripts for every conversation just to render a sidebar would scale
    badly once an account has more than a handful of threads.
    """
    return crud.list_conversations(db, current_user.id)


@router.get("/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(
    conversation_id: uuid.UUID,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_conversation(db, current_user.id, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    messages = crud.list_conversation_messages(db, conversation.id)
    return _to_out(conversation, messages)


@router.post("", response_model=schemas.ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: schemas.ConversationCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.create_conversation(db, current_user.id, payload)
    return _to_out(conversation, [])


@router.put("/{conversation_id}", response_model=schemas.ConversationOut)
def replace_conversation(
    conversation_id: uuid.UUID,
    payload: schemas.ConversationUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full-replace, matching how the client already treats a conversation
    as one blob (see schemas.ConversationUpdate) -- every turn re-sends the
    complete title/category/messages, not just a delta.
    """
    conversation = crud.get_conversation(db, current_user.id, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    conversation = crud.replace_conversation(db, conversation, payload)
    messages = crud.list_conversation_messages(db, conversation.id)
    return _to_out(conversation, messages)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = crud.get_conversation(db, current_user.id, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    crud.delete_conversation(db, conversation)
