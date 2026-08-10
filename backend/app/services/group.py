"""Group management: create/list/rename/delete with case-insensitive naming.

Business rules (duplicate resolution, name-conflict detection, orphan counting)
live here; the repositories underneath are pure CRUD.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.conversation import ConversationRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.group import GroupRepository
from app.models.group import Group


class GroupNotFound(Exception):
    """Raised when a group_id does not exist or does not belong to the caller."""


class GroupNameConflict(Exception):
    """Raised when a rename target collides (case-insensitively) with another group."""


class GroupService:
    def __init__(
        self,
        session: AsyncSession,
        groups: GroupRepository,
        conversations: ConversationRepository,
        documents: DocumentRepository,
    ) -> None:
        self._session = session
        self._groups = groups
        self._conversations = conversations
        self._documents = documents

    async def create(self, user_id: uuid.UUID, name: str) -> Group:
        # Duplicate (case-insensitive) resolves to the existing group rather than
        # erroring — supports inline "create or pick" in the upload/sidebar UIs.
        name = name.strip()
        existing = await self._groups.get_by_name(user_id, name)
        if existing is not None:
            return existing
        group = await self._groups.create(user_id=user_id, name=name)
        await self._session.commit()
        return group

    async def list(self, user_id: uuid.UUID) -> list[Group]:
        return await self._groups.list_for_user(user_id)

    async def rename(self, group_id: uuid.UUID, user_id: uuid.UUID, name: str) -> Group:
        name = name.strip()
        group = await self._groups.get_for_user(group_id, user_id)
        if group is None:
            raise GroupNotFound(str(group_id))
        clash = await self._groups.get_by_name(user_id, name)
        if clash is not None and clash.id != group_id:
            raise GroupNameConflict(name)
        await self._groups.rename(group_id, name)
        await self._session.commit()
        # Re-fetch so the response reflects the committed name/updated_at.
        refreshed = await self._groups.get_for_user(group_id, user_id)
        assert refreshed is not None  # just renamed it in this session
        return refreshed

    async def delete(self, group_id: uuid.UUID, user_id: uuid.UUID) -> tuple[int, int]:
        """Delete the group; its chats/docs orphan to ungrouped via the FK's SET NULL.

        Returns (chats_ungrouped, documents_ungrouped) counted BEFORE the delete.
        """
        group = await self._groups.get_for_user(group_id, user_id)
        if group is None:
            raise GroupNotFound(str(group_id))
        chats = await self._conversations.count_by_group(group_id)
        docs = await self._documents.count_by_group(group_id)
        await self._groups.delete(group_id)
        await self._session.commit()
        return chats, docs
