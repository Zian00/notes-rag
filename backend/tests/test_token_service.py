import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.core.security import TokenService


def test_access_token_roundtrip():
    svc = TokenService()
    uid = uuid.uuid4()
    token = svc.create_access_token(uid)
    assert svc.decode_access_token(token) == uid


def test_decode_rejects_bad_signature():
    svc = TokenService()
    token = svc.create_access_token(uuid.uuid4())
    with pytest.raises(jwt.InvalidTokenError):
        svc.decode_access_token(token + "tampered")


def test_decode_rejects_wrong_type():
    svc = TokenService()
    uid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    bad = jwt.encode(
        {"sub": str(uid), "iat": now, "exp": now + timedelta(minutes=5), "type": "refresh"},
        svc._secret,  # noqa: SLF001
        algorithm=svc._alg,  # noqa: SLF001
    )
    with pytest.raises(jwt.InvalidTokenError):
        svc.decode_access_token(bad)


def test_refresh_token_generation_and_hash():
    svc = TokenService()
    raw, token_hash, expires_at = svc.generate_refresh_token()
    assert raw and token_hash and token_hash != raw
    assert svc.hash_refresh_token(raw) == token_hash
    assert expires_at > datetime.now(tz=UTC)


def test_decode_rejects_expired_token():
    svc = TokenService()
    now = datetime.now(tz=UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": now - timedelta(hours=1),
            "exp": now - timedelta(minutes=1),
            "type": "access",
        },
        svc._secret,  # noqa: SLF001
        algorithm=svc._alg,  # noqa: SLF001
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        svc.decode_access_token(expired)


def test_decode_rejects_malformed_subject():
    svc = TokenService()
    now = datetime.now(tz=UTC)
    bad_sub = jwt.encode(
        {"sub": "not-a-uuid", "iat": now, "exp": now + timedelta(minutes=5), "type": "access"},
        svc._secret,  # noqa: SLF001
        algorithm=svc._alg,  # noqa: SLF001
    )
    with pytest.raises(jwt.InvalidTokenError):
        svc.decode_access_token(bad_sub)
