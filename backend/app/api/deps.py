import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import PasswordHasher, TokenService
from app.db.repositories.refresh_token import RefreshTokenRepository
from app.db.repositories.user import UserRepository
from app.db.session import get_db
from app.models.user import User
from app.services.auth import AuthService

_bearer = HTTPBearer(auto_error=False)


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:  # noqa: B008
    return AuthService(
        session,
        UserRepository(session),
        RefreshTokenRepository(session),
        PasswordHasher(),
        TokenService(),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        user_id = TokenService().decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return user
