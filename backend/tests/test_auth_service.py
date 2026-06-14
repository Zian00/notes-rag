import uuid

import pytest
from app.core.security import PasswordHasher, TokenService
from app.db.repositories.refresh_token import RefreshTokenRepository
from app.db.repositories.user import UserRepository
from app.services.auth import (
    AuthService,
    EmailAlreadyExists,
    InvalidCredentials,
    InvalidRefreshToken,
)


def _service(db_session) -> AuthService:
    return AuthService(
        db_session,
        UserRepository(db_session),
        RefreshTokenRepository(db_session),
        PasswordHasher(),
        TokenService(),
    )


def _email() -> str:
    return f"user-{uuid.uuid4().hex}@example.com"


@pytest.mark.asyncio
async def test_register_creates_hashed_user(db_session):
    svc = _service(db_session)
    email = _email()
    user = await svc.register(email, "password123")
    assert user.email == email
    assert user.hashed_password != "password123"


@pytest.mark.asyncio
async def test_register_duplicate_email_raises(db_session):
    svc = _service(db_session)
    email = _email()
    await svc.register(email, "password123")
    with pytest.raises(EmailAlreadyExists):
        await svc.register(email, "password123")


@pytest.mark.asyncio
async def test_authenticate_ok_and_bad(db_session):
    svc = _service(db_session)
    email = _email()
    await svc.register(email, "password123")

    user = await svc.authenticate(email, "password123")
    assert user.email == email

    with pytest.raises(InvalidCredentials):
        await svc.authenticate(email, "wrong-password")
    with pytest.raises(InvalidCredentials):
        await svc.authenticate("nobody@example.com", "password123")


@pytest.mark.asyncio
async def test_refresh_rotates_and_old_is_revoked(db_session):
    svc = _service(db_session)
    user = await svc.register(_email(), "password123")
    _access, raw1 = await svc.issue_tokens(user)

    _access2, raw2 = await svc.refresh(raw1)
    assert raw2 != raw1

    with pytest.raises(InvalidRefreshToken):
        await svc.refresh(raw1)


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_all(db_session):
    svc = _service(db_session)
    user = await svc.register(_email(), "password123")
    _access, raw1 = await svc.issue_tokens(user)
    _access2, raw2 = await svc.refresh(raw1)

    with pytest.raises(InvalidRefreshToken):
        await svc.refresh(raw1)
    with pytest.raises(InvalidRefreshToken):
        await svc.refresh(raw2)


@pytest.mark.asyncio
async def test_logout_revokes(db_session):
    svc = _service(db_session)
    user = await svc.register(_email(), "password123")
    _access, raw = await svc.issue_tokens(user)

    await svc.logout(raw)
    with pytest.raises(InvalidRefreshToken):
        await svc.refresh(raw)


@pytest.mark.asyncio
async def test_issue_tokens_purges_expired_refresh_tokens(db_session):
    from datetime import UTC, datetime, timedelta

    svc = _service(db_session)
    user = await svc.register(_email(), "password123")

    # Seed an already-expired refresh token for this user.
    repo = RefreshTokenRepository(db_session)
    await repo.create(
        user_id=user.id,
        token_hash="stale-expired-hash",
        expires_at=datetime.now(tz=UTC) - timedelta(days=1),
    )
    await db_session.commit()
    assert await repo.get_by_hash("stale-expired-hash") is not None

    # Issuing new tokens runs opportunistic cleanup for the user.
    await svc.issue_tokens(user)

    assert await repo.get_by_hash("stale-expired-hash") is None
