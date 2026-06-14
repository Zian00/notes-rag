from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from app.api.deps import get_auth_service, get_current_user
from app.core.config import get_settings
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth import (
    AuthService,
    EmailAlreadyExists,
    InvalidCredentials,
    InvalidRefreshToken,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
_COOKIE_PATH = "/auth"


def _set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(_REFRESH_COOKIE, path=_COOKIE_PATH)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, service: AuthService = Depends(get_auth_service)  # noqa: B008
) -> User:
    try:
        return await service.register(body.email, body.password)
    except EmailAlreadyExists as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),  # noqa: B008
) -> TokenResponse:
    try:
        user = await service.authenticate(body.email, body.password)
    except InvalidCredentials as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password") from exc
    access, raw = await service.issue_tokens(user)
    _set_refresh_cookie(response, raw)
    return TokenResponse(access_token=access)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),  # noqa: B008
    service: AuthService = Depends(get_auth_service),  # noqa: B008
) -> TokenResponse:
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token")
    try:
        access, raw = await service.refresh(refresh_token)
    except InvalidRefreshToken as exc:
        _clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from exc
    _set_refresh_cookie(response, raw)
    return TokenResponse(access_token=access)


@router.post("/logout")
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None),  # noqa: B008
    service: AuthService = Depends(get_auth_service),  # noqa: B008
) -> dict[str, str]:
    await service.logout(refresh_token)
    _clear_refresh_cookie(response)
    return {"detail": "logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> User:  # noqa: B008
    return current_user
