from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PasswordHasher, TokenService
from app.db.repositories.refresh_token import RefreshTokenRepository
from app.db.repositories.user import UserRepository
from app.models.user import User
from app.utils.time import utcnow


class AuthError(Exception):
    """Base class for auth domain errors."""


class EmailAlreadyExists(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


class InvalidRefreshToken(AuthError):
    pass


class AuthService:
    """Auth business logic. Owns its transaction (commits explicitly)."""

    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
    ) -> None:
        self._session = session
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._hasher = hasher
        self._tokens = tokens

    async def register(self, email: str, password: str) -> User:
        email = email.lower()
        if await self._users.get_by_email(email) is not None:
            raise EmailAlreadyExists(email)
        user = await self._users.create(
            email=email, hashed_password=self._hasher.hash(password)
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:  # race on unique email
            await self._session.rollback()
            raise EmailAlreadyExists(email) from exc
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self._users.get_by_email(email.lower())
        if user is None or not self._hasher.verify(password, user.hashed_password):
            raise InvalidCredentials
        if not user.is_active:
            raise InvalidCredentials
        return user

    async def issue_tokens(self, user: User) -> tuple[str, str]:
        """Return (access_token, raw_refresh_token) and persist the refresh token."""
        await self._refresh_tokens.delete_expired_for_user(user.id)
        raw, token_hash, expires_at = self._tokens.generate_refresh_token()
        await self._refresh_tokens.create(
            user_id=user.id, token_hash=token_hash, expires_at=expires_at
        )
        access = self._tokens.create_access_token(user.id)
        await self._session.commit()
        return access, raw

    async def refresh(self, raw_refresh: str) -> tuple[str, str]:
        token_hash = self._tokens.hash_refresh_token(raw_refresh)
        row = await self._refresh_tokens.get_by_hash(token_hash)
        if row is None:
            raise InvalidRefreshToken
        if row.revoked_at is not None:
            # Reuse of an already-revoked token -> likely theft. Revoke all and commit.
            await self._refresh_tokens.revoke_all_for_user(row.user_id)
            await self._session.commit()
            raise InvalidRefreshToken
        if row.expires_at <= utcnow():
            raise InvalidRefreshToken

        user = await self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshToken

        await self._refresh_tokens.revoke(row)
        return await self.issue_tokens(user)

    async def logout(self, raw_refresh: str | None) -> None:
        if not raw_refresh:
            return
        row = await self._refresh_tokens.get_by_hash(
            self._tokens.hash_refresh_token(raw_refresh)
        )
        if row is not None and row.revoked_at is None:
            await self._refresh_tokens.revoke(row)
            await self._session.commit()
