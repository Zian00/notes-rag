from app.core.security import PasswordHasher


def test_hash_is_not_plaintext_and_verifies():
    hasher = PasswordHasher()
    hashed = hasher.hash("s3cret-password")

    assert hashed != "s3cret-password"
    assert hasher.verify("s3cret-password", hashed) is True


def test_verify_rejects_wrong_password():
    hasher = PasswordHasher()
    hashed = hasher.hash("s3cret-password")

    assert hasher.verify("wrong", hashed) is False


def test_same_password_hashes_differently():
    hasher = PasswordHasher()
    assert hasher.hash("abc12345") != hasher.hash("abc12345")  # random salt
