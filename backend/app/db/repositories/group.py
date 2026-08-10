import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.group import Group


class GroupRepository(BaseRepository[Group]):
    """CRUD for groups. Validation/duplicate-resolution live in the service layer."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Group, session)

    async def create(self, *, user_id: uuid.UUID, name: str) -> Group:  # type: ignore[override]
        group = Group(user_id=user_id, name=name)
        self._session.add(group)
        await self._session.flush()  # populate id
        return group

    async def get_for_user(self, group_id: uuid.UUID, user_id: uuid.UUID) -> Group | None:
        stmt = select(Group).where(Group.id == group_id, Group.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_name(self, user_id: uuid.UUID, name: str) -> Group | None:
        # Case-insensitive lookup — lets the service resolve a duplicate create to the
        # existing group instead of erroring on the unique index.
        stmt = select(Group).where(
            Group.user_id == user_id, func.lower(Group.name) == name.strip().lower()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Group]:
        stmt = (
            select(Group).where(Group.user_id == user_id).order_by(func.lower(Group.name))
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def rename(self, group_id: uuid.UUID, name: str) -> None:
        await self._session.execute(
            update(Group).where(Group.id == group_id).values(name=name, updated_at=func.now())
        )

    async def delete(self, group_id: uuid.UUID) -> None:  # type: ignore[override]
        # Deleting the row nulls group_id on its chats/documents via the FK's
        # ON DELETE SET NULL (orphan-to-ungrouped) — no manual fan-out needed.
        group = await self._session.get(Group, group_id)
        if group is not None:
            await self._session.delete(group)
