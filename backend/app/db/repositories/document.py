import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.document import Document


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Document, session)

    async def list_for_user(
        self, user_id: uuid.UUID, course: str | None = None
    ) -> list[Document]:
        stmt = select(Document).where(Document.user_id == user_id)
        if course is not None:
            stmt = stmt.where(Document.course == course)
        stmt = stmt.order_by(Document.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_user(
        self, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> Document | None:
        stmt = select(Document).where(
            Document.id == document_id, Document.user_id == user_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_and_hash(
        self, user_id: uuid.UUID, content_hash: str
    ) -> Document | None:
        stmt = select(Document).where(
            Document.user_id == user_id, Document.content_hash == content_hash
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
