import pytest


@pytest.mark.asyncio
async def test_upload_list_delete_flow(auth_client):
    files = {"file": ("notes.txt", b"alpha beta gamma. delta epsilon.", "text/plain")}
    resp = await auth_client.post(
        "/documents", files=files, data={"title": "My Notes", "course": "BIO"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "My Notes"
    assert body["chunk_count"] >= 1
    doc_id = body["id"]

    listed = await auth_client.get("/documents")
    assert listed.status_code == 200
    assert any(d["id"] == doc_id for d in listed.json())

    filtered = await auth_client.get("/documents", params={"course": "BIO"})
    assert [d["id"] for d in filtered.json()] == [doc_id]

    deleted = await auth_client.delete(f"/documents/{doc_id}")
    assert deleted.status_code == 204
    after = await auth_client.get("/documents")
    assert all(d["id"] != doc_id for d in after.json())


@pytest.mark.asyncio
async def test_upload_requires_auth(client):
    files = {"file": ("notes.txt", b"hi", "text/plain")}
    resp = await client.post("/documents", files=files)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unsupported_type_rejected(auth_client):
    files = {"file": ("evil.exe", b"MZ\x90\x00bad", "application/octet-stream")}
    resp = await auth_client.post("/documents", files=files)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_upload_returns_409(auth_client):
    data = b"identical content for dedup"
    files = {"file": ("a.txt", data, "text/plain")}
    first = await auth_client.post("/documents", files=files)
    assert first.status_code == 201
    again = await auth_client.post(
        "/documents", files={"file": ("a.txt", data, "text/plain")}
    )
    assert again.status_code == 409
    assert again.json()["document_id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_delete_not_owned_returns_404(auth_client, client):
    files = {"file": ("a.txt", b"owner content", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    import uuid as _uuid

    email = f"other-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = (await client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    )).json()["access_token"]
    resp = await client.delete(
        f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
