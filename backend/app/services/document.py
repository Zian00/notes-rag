"""Document metadata operations that need group-ownership validation.

Assigning a document to a group (on upload, on replace, or via a metadata edit)
must verify the target group belongs to the caller — that business rule lives here;
the repositories underneath stay pure CRUD.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.document import UNSET, DocumentRepository
from app.db.repositories.group import GroupRepository
from app.models.document import Document
from app.services.group import GroupNotFound


class DocumentNotFound(Exception):
    """Raised when a document_id does not exist or does not belong to the caller."""


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        documents: DocumentRepository,
        groups: GroupRepository,
    ) -> None:
        self._session = session
        self._documents = documents
        self._groups = groups

    async def ensure_group_owned(
        self, group_id: uuid.UUID | None, user_id: uuid.UUID
    ) -> None:
        """Raise GroupNotFound unless the group belongs to the user. None (ungrouped)
        is always allowed — there is no group to validate."""
        if group_id is not None and await self._groups.get_for_user(group_id, user_id) is None:
            raise GroupNotFound(str(group_id))

    async def update_metadata(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        group_id: uuid.UUID | None = UNSET,
        tags: list[str] | None = UNSET,
    ) -> Document:
        """Edit a document's group and/or tags (ownership-checked); return the updated row.

        group_id=None ungroups; a non-None group_id must belong to the caller. Fields
        left UNSET are untouched. Raises DocumentNotFound / GroupNotFound.
        """
        doc = await self._documents.get_for_user(document_id, user_id)
        if doc is None:
            raise DocumentNotFound(str(document_id))
        if group_id is not UNSET:
            await self.ensure_group_owned(group_id, user_id)
        await self._documents.update_metadata(document_id, group_id=group_id, tags=tags)
        await self._session.commit()
        # Re-fetch: commit expired the instance, so reload the committed state.
        refreshed = await self._documents.get_for_user(document_id, user_id)
        assert refreshed is not None  # just updated it in this session
        return refreshed
