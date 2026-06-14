from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):  # noqa: UP046
    """Generic async CRUD repository for a single ORM model.

    Persistence only — no business logic (that belongs in services).
    """

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def create(self, **values: object) -> ModelT:
        instance = self._model(**values)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def get(self, id_: Any) -> ModelT | None:
        """Fetch by primary key (int, UUID, etc.)."""
        return await self._session.get(self._model, id_)

    async def list(self) -> list[ModelT]:
        result = await self._session.execute(select(self._model))
        return list(result.scalars().all())

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)
        await self._session.flush()
