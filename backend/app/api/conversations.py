import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_chat_service, get_current_user
from app.models.user import User
from app.schemas.chat import ConversationDetail, ConversationResponse
from app.services.chat import ChatService, ConversationNotFound

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> list[ConversationResponse]:
    return await service.list_conversations(current_user.id)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> ConversationDetail:
    try:
        data = await service.get_detail(conversation_id, current_user.id)
    except ConversationNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    convo = data["conversation"]
    return ConversationDetail(
        id=convo.id,
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        messages=data["messages"],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> None:
    try:
        await service.delete_conversation(conversation_id, current_user.id)
    except ConversationNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
