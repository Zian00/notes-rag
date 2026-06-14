import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import Argon2Error

from app.core.config import get_settings


class PasswordHasher:
    """argon2id password hashing adapter."""

    def __init__(self) -> None:
        self._hasher = _Argon2Hasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        # Any argon2 failure on verify (mismatch OR a malformed/corrupt stored
        # hash) means the password is not valid — never let it 500.
        try:
            self._hasher.verify(hashed, password)
            return True
        except Argon2Error:
            return False


class TokenService:
    """JWT access tokens + opaque (hashed) refresh tokens."""

    def __init__(self) -> None:
        settings = get_settings()
        self._secret = settings.jwt_secret
        self._alg = settings.jwt_algorithm
        self._access_ttl = timedelta(minutes=settings.access_token_expire_minutes)
        self._refresh_ttl = timedelta(days=settings.refresh_token_expire_days)

    def create_access_token(self, user_id: uuid.UUID) -> str:
        now = datetime.now(tz=UTC)
        payload = {
            "sub": str(user_id),
            "iat": now,
            "exp": now + self._access_ttl,
            "type": "access",
        }
        return jwt.encode(payload, self._secret, algorithm=self._alg)

    def decode_access_token(self, token: str) -> uuid.UUID:
        payload = jwt.decode(token, self._secret, algorithms=[self._alg])
        if payload.get("type") != "access":
            raise jwt.InvalidTokenError("not an access token")
        sub = payload.get("sub")
        if not sub:
            raise jwt.InvalidTokenError("missing subject")
        try:
            return uuid.UUID(sub)
        except (ValueError, TypeError) as exc:
            raise jwt.InvalidTokenError("invalid subject") from exc

    def generate_refresh_token(self) -> tuple[str, str, datetime]:
        raw = secrets.token_urlsafe(32)
        return raw, self.hash_refresh_token(raw), datetime.now(tz=UTC) + self._refresh_ttl

    @staticmethod
    def hash_refresh_token(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()
