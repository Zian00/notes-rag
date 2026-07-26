import inspect

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
    # stage() only persists the document row; chunking/embedding now happens later
    # in the background process() job, so right after upload the doc is 'pending'.
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
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
async def test_upload_returns_pending_status_and_enqueues_processing(auth_client):
    from app.api import deps

    enqueued: list[str] = []

    async def fake_enqueue(document_id):
        enqueued.append(str(document_id))

    auth_client.app.dependency_overrides[deps.get_enqueue_processing] = lambda: fake_enqueue

    r = await auth_client.post(
        "/documents",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
    assert enqueued == [body["id"]]

    auth_client.app.dependency_overrides.pop(deps.get_enqueue_processing, None)


def test_upload_document_wires_real_enqueue_processing_dependency():
    """Regression guard for the enqueue wiring itself (not the queue mechanics).

    Doesn't go through the app/ASGITransport — that path always sees either the
    conftest no-op override or a test's own fake, so it can never catch a
    regression where `documents.py` starts depending on the wrong callable. This
    inspects the route's actual Depends(...) graph and the real `deps` module
    directly: the ``enqueue`` parameter's dependency must literally be
    ``deps.get_enqueue_processing`` (not a copy/lookalike), and that provider
    must return the real ``deps.enqueue_document_processing`` (not a fake).
    """
    from app.api import deps, documents

    sig = inspect.signature(documents.upload_document)
    enqueue_param = sig.parameters["enqueue"]
    assert enqueue_param.default.dependency is deps.get_enqueue_processing
    assert deps.get_enqueue_processing() is deps.enqueue_document_processing


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
