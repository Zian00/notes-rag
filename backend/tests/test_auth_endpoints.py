import uuid

import pytest


def _email() -> str:
    return f"user-{uuid.uuid4().hex}@example.com"


@pytest.mark.asyncio
async def test_register_then_duplicate(client):
    email = _email()
    r1 = await client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r1.status_code == 201
    body = r1.json()
    assert body["email"] == email and "id" in body

    r2 = await client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_register_validation(client):
    r = await client.post("/auth/register", json={"email": "bad", "password": "x"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_login_sets_cookie_and_returns_access(client):
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "password123"})

    r = await client.post("/auth/login", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    assert r.json()["access_token"]
    assert "refresh_token" in r.cookies


@pytest.mark.asyncio
async def test_login_cookie_scoped_to_root_path(client):
    # The refresh cookie must be scoped to Path=/ (not /auth). In dev the browser
    # reaches the API through the Vite /api proxy, so it only ever sees paths like
    # /api/auth/refresh — a cookie scoped to /auth would never match and the browser
    # would drop it, silently logging the user out on every reload.
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "password123"})

    r = await client.post("/auth/login", json={"email": email, "password": "password123"})
    set_cookie = r.headers["set-cookie"].lower()
    assert "path=/;" in set_cookie or set_cookie.rstrip().endswith("path=/")


@pytest.mark.asyncio
async def test_login_bad_credentials(client):
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    r = await client.post("/auth/login", json={"email": email, "password": "nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_token(client):
    assert (await client.get("/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(client):
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    login = await client.post("/auth/login", json={"email": email, "password": "password123"})
    access = login.json()["access_token"]

    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["email"] == email


@pytest.mark.asyncio
async def test_refresh_rotates_cookie(client):
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    login = await client.post("/auth/login", json={"email": email, "password": "password123"})
    old_cookie = login.cookies["refresh_token"]

    r = await client.post("/auth/refresh")
    assert r.status_code == 200
    assert r.json()["access_token"]
    assert client.cookies["refresh_token"] != old_cookie


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    await client.post("/auth/login", json={"email": email, "password": "password123"})

    r = await client.post("/auth/logout")
    assert r.status_code == 200
    client.cookies.clear()
    assert (await client.post("/auth/refresh")).status_code == 401
