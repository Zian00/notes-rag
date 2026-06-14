import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.refresh_token import RefreshToken


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(RefreshToken, session)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(tz=UTC)
        await self._session.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(tz=UTC))
        )

    async def delete_expired_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.expires_at < datetime.now(tz=UTC),
            )
        )
