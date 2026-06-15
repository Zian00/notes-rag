import pytest


@pytest.mark.asyncio
async def test_search_returns_user_chunks(auth_client):
    files = {"file": ("notes.txt", b"mitochondria is the powerhouse of the cell.", "text/plain")}
    assert (await auth_client.post("/documents", files=files)).status_code == 201

    resp = await auth_client.post("/search", json={"query": "what is the powerhouse", "top_k": 3})
    assert resp.status_code == 200, resp.text
    matches = resp.json()
    assert len(matches) >= 1
    assert "filename" in matches[0] and "score" in matches[0]


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    resp = await client.post("/search", json={"query": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_only_sees_own_documents(auth_client, client):
    await auth_client.post(
        "/documents", files={"file": ("a.txt", b"private notes content", "text/plain")}
    )
    import uuid as _uuid

    email = f"other-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = (await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )).json()["access_token"]
    resp = await client.post(
        "/search", json={"query": "private notes"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == []
