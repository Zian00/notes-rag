"""POST /chat — streams a grounded, cited answer as Server-Sent Events.

Ownership refinement: if the caller supplies an existing conversation_id, we check
ownership BEFORE returning the StreamingResponse.  This gives a real HTTP 404 (not an
in-stream error frame) and lets the client distinguish "bad conversation id" from
mid-stream failures without having to parse SSE error frames.  New conversations
(conversation_id=None) skip this check and stream normally.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_service, get_current_user
from app.models.user import User
from app.schemas.chat import ChatRequest
from app.services.chat import ChatService, ConversationNotFound

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: ChatService = Depends(get_chat_service),  # noqa: B008
) -> StreamingResponse:
    # Pre-stream ownership check: if a conversation_id is supplied and the caller does
    # not own it, raise 404 here rather than letting the error surface inside the stream.
    # New conversations (None) are created inside stream_answer itself.
    if body.conversation_id is not None:
        try:
            await service.verify_ownership(body.conversation_id, current_user.id)
        except ConversationNotFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None

    stream = service.stream_answer(
        user_id=current_user.id,
        conversation_id=body.conversation_id,
        question=body.question,
        course=body.course,
        tags=body.tags,
        top_k=body.top_k,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            # Without these, an intermediary (the browser, Vite's dev proxy) can
            # buffer the whole response instead of flushing each SSE frame as
            # it's produced, making token-by-token streaming appear all-at-once.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
