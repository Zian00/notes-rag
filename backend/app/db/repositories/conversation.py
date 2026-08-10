import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.conversation import Conversation

# Sentinel distinguishing "field omitted" from "field set to None" in partial updates —
# None is a real value for group_id (ungroup), so it can't double as "leave unchanged".
UNSET: Any = object()


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Conversation, session)

    async def create(  # type: ignore[override]
        self, *, user_id: uuid.UUID, title: str | None
    ) -> Conversation:
        # Overrides BaseRepository.create(**values) with a typed signature for safety.
        convo = Conversation(user_id=user_id, title=title)
        self._session.add(convo)
        await self._session.flush()  # populate id
        return convo

    async def get_for_user(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_by_group(self, group_id: uuid.UUID) -> int:
        stmt = (
            select(func.count()).select_from(Conversation).where(Conversation.group_id == group_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def update_fields(
        self,
        conversation_id: uuid.UUID,
        *,
        title: Any = UNSET,
        group_id: uuid.UUID | None = UNSET,
    ) -> None:
        """Partial update: only the fields passed (i.e. not UNSET) are written."""
        values: dict[str, Any] = {}
        if title is not UNSET:
            values["title"] = title
        if group_id is not UNSET:
            values["group_id"] = group_id
        if not values:
            return
        await self._session.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(**values)
        )

    async def touch(self, conversation_id: uuid.UUID) -> None:
        # Bump updated_at so the conversation rises to the top of the list after a turn.
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )

    async def delete(self, conversation_id: uuid.UUID) -> None:  # type: ignore[override]
        # Overrides BaseRepository.delete(instance) to accept a UUID for convenience.
        convo = await self._session.get(Conversation, conversation_id)
        if convo is not None:
            await self._session.delete(convo)
