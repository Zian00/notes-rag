import pytest
from app.schemas.auth import LoginRequest, RegisterRequest
from pydantic import ValidationError


def test_register_requires_valid_email():
    with pytest.raises(ValidationError):
        RegisterRequest(email="not-an-email", password="longenough")


def test_register_password_min_length():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@b.com", password="short")


def test_login_request_ok():
    req = LoginRequest(email="a@b.com", password="whatever")
    assert req.email == "a@b.com"
