import inspect
import uuid

import pytest


async def _process_synchronously(document_id, client) -> None:
    """Run IngestionService.process() inline against the same test DB/fakes the
    HTTP client uses (mirrors test_search_api.py's helper) — Replace now rejects
    documents still 'pending'/'processing' (issue 9's race guard), so any test
    that replaces a just-uploaded document must first actually get it to 'ready',
    since the default `client` fixture's enqueue override is a no-op."""
    from app.core.config import get_settings
    from app.db.repositories.chunk import ChunkRepository
    from app.db.repositories.document import DocumentRepository
    from app.rag.chunking import Chunker
    from app.rag.parsing import ParserDispatcher
    from app.rag.storage import LocalFileStorage
    from app.services.ingestion import IngestionService

    from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider

    settings = get_settings()
    async with client.maker() as session:
        service = IngestionService(
            session=session,
            documents=DocumentRepository(session),
            chunks=ChunkRepository(session),
            storage=LocalFileStorage(client.upload_dir),
            parser=ParserDispatcher(
                ocr=FakeOcrProvider(),
                ocr_enabled=settings.ocr_enabled,
                min_chars=settings.pdf_ocr_min_chars_per_page,
            ),
            chunker=Chunker(
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
            ),
            embeddings=FakeEmbeddingsProvider(),
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
        )
        await service.process(document_id)


def _enqueue_synchronously(client):
    """Override for get_enqueue_processing: process the document inline instead
    of deferring to a real job queue (there's no worker running in tests)."""
    from app.api import deps

    client.app.dependency_overrides[deps.get_enqueue_processing] = (
        lambda: (lambda document_id: _process_synchronously(document_id, client))
    )


@pytest.mark.asyncio
async def test_upload_list_delete_flow(auth_client):
    # Delete now rejects a still-pending/processing document (race guard) — process
    # it synchronously first so it reaches 'ready', matching real-world sequencing.
    _enqueue_synchronously(auth_client)
    gid = (await auth_client.post("/groups", json={"name": "BIO"})).json()["id"]
    files = {"file": ("notes.txt", b"alpha beta gamma. delta epsilon.", "text/plain")}
    resp = await auth_client.post(
        "/documents", files=files, data={"title": "My Notes", "group_id": gid}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "My Notes"
    assert body["group_id"] == gid  # upload assigned the group
    # stage() only persists the document row; chunking/embedding now happens later
    # in the background process() job, so right after upload the doc is 'pending'.
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
    doc_id = body["id"]

    listed = await auth_client.get("/documents")
    assert listed.status_code == 200
    assert any(d["id"] == doc_id for d in listed.json())

    filtered = await auth_client.get("/documents", params={"group_id": gid})
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
    again = await auth_client.post("/documents", files={"file": ("a.txt", data, "text/plain")})
    assert again.status_code == 409
    assert again.json()["document_id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_patch_document_group_and_tags(auth_client):
    """PATCH edits a document's group + tags; explicit null ungroups; omitted is untouched."""
    gid = (await auth_client.post("/groups", json={"name": "CS"})).json()["id"]
    files = {"file": ("n.txt", b"hello world", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    resp = await auth_client.patch(
        f"/documents/{doc_id}", json={"group_id": gid, "tags": ["a", "b"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["group_id"] == gid
    assert resp.json()["tags"] == ["a", "b"]

    # Explicit null ungroups; tags omitted → left as-is.
    resp = await auth_client.patch(f"/documents/{doc_id}", json={"group_id": None})
    assert resp.status_code == 200
    assert resp.json()["group_id"] is None
    assert resp.json()["tags"] == ["a", "b"]


@pytest.mark.asyncio
async def test_document_group_must_belong_to_user(auth_client):
    """Upload into, or PATCH into, another user's group is rejected with 404."""
    token_a = auth_client.headers["Authorization"]

    # User B owns a group A must not be able to target.
    b_email = f"docs-b-{uuid.uuid4().hex}@example.com"
    await auth_client.post("/auth/register", json={"email": b_email, "password": "password123"})
    token_b = (
        await auth_client.post("/auth/login", json={"email": b_email, "password": "password123"})
    ).json()["access_token"]
    auth_client.headers["Authorization"] = f"Bearer {token_b}"
    b_gid = (await auth_client.post("/groups", json={"name": "B-group"})).json()["id"]

    # Back to A: uploading into B's group → 404 (and a non-existent group → 404).
    auth_client.headers["Authorization"] = token_a
    files = {"file": ("n.txt", b"hello world", "text/plain")}
    resp = await auth_client.post("/documents", files=files, data={"group_id": b_gid})
    assert resp.status_code == 404

    other = {"file": ("m.txt", b"other data", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=other)).json()["id"]
    assert (
        await auth_client.patch(f"/documents/{doc_id}", json={"group_id": b_gid})
    ).status_code == 404
    assert (
        await auth_client.patch(f"/documents/{doc_id}", json={"group_id": str(uuid.uuid4())})
    ).status_code == 404


@pytest.mark.asyncio
async def test_list_documents_group_filter_must_belong_to_user(auth_client):
    """GET /documents?group_id=<foreign or nonexistent> 404s instead of silently
    returning an empty list, same validation as upload/PATCH."""
    token_a = auth_client.headers["Authorization"]

    b_email = f"docs-list-b-{uuid.uuid4().hex}@example.com"
    await auth_client.post("/auth/register", json={"email": b_email, "password": "password123"})
    token_b = (
        await auth_client.post("/auth/login", json={"email": b_email, "password": "password123"})
    ).json()["access_token"]
    auth_client.headers["Authorization"] = f"Bearer {token_b}"
    b_gid = (await auth_client.post("/groups", json={"name": "B-list-group"})).json()["id"]

    auth_client.headers["Authorization"] = token_a
    assert (await auth_client.get(f"/documents?group_id={b_gid}")).status_code == 404
    assert (
        await auth_client.get(f"/documents?group_id={uuid.uuid4()}")
    ).status_code == 404

    # A's own group filters normally (no false positive from the ownership check).
    a_gid = (await auth_client.post("/groups", json={"name": "A-list-group"})).json()["id"]
    resp = await auth_client.get(f"/documents?group_id={a_gid}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_patch_document_not_owned_404(auth_client):
    """User B cannot PATCH user A's document."""
    files = {"file": ("n.txt", b"hello world", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    b_email = f"docs-iso-{uuid.uuid4().hex}@example.com"
    await auth_client.post("/auth/register", json={"email": b_email, "password": "password123"})
    token_b = (
        await auth_client.post("/auth/login", json={"email": b_email, "password": "password123"})
    ).json()["access_token"]
    auth_client.headers["Authorization"] = f"Bearer {token_b}"

    assert (
        await auth_client.patch(f"/documents/{doc_id}", json={"tags": ["x"]})
    ).status_code == 404


@pytest.mark.asyncio
async def test_replace_with_identical_content_short_circuits(auth_client):
    _enqueue_synchronously(auth_client)
    upload = await auth_client.post(
        "/documents",
        files={"file": ("notes.txt", b"same content", "text/plain")},
    )
    document_id = upload.json()["id"]

    r = await auth_client.post(
        f"/documents/{document_id}/replace",
        files={"file": ("notes.txt", b"same content", "text/plain")},
    )

    assert r.status_code == 200, r.text
    assert r.json()["no_changes"] is True


@pytest.mark.asyncio
async def test_replace_with_new_content_enqueues_processing(auth_client):
    from app.api import deps

    enqueued: list[tuple] = []

    async def fake_enqueue(document_id, new_storage_path, new_content_hash, new_file_size):
        enqueued.append((str(document_id), new_content_hash))

    auth_client.app.dependency_overrides[deps.get_enqueue_replace] = lambda: fake_enqueue
    _enqueue_synchronously(auth_client)

    upload = await auth_client.post(
        "/documents",
        files={"file": ("notes.txt", b"original content", "text/plain")},
    )
    document_id = upload.json()["id"]

    r = await auth_client.post(
        f"/documents/{document_id}/replace",
        files={"file": ("notes.txt", b"changed content", "text/plain")},
    )

    assert r.status_code == 200, r.text
    assert r.json()["no_changes"] is False
    assert len(enqueued) == 1
    assert enqueued[0][0] == document_id

    auth_client.app.dependency_overrides.pop(deps.get_enqueue_replace, None)


@pytest.mark.asyncio
async def test_replace_can_ungroup_via_explicit_flag(auth_client):
    """Multipart form fields can't express "explicit null" like a JSON PATCH body
    can, so clearing a document's group on /replace needs its own `ungroup` flag
    rather than overloading group_id=None (which just means "not provided")."""
    # Identical content short-circuits before enqueueing a background job (see
    # test_replace_with_identical_content_short_circuits) — irrelevant to what's
    # under test here (the metadata update, which happens before that check).
    _enqueue_synchronously(auth_client)
    gid = (await auth_client.post("/groups", json={"name": "Ungroup-me"})).json()["id"]
    upload = await auth_client.post(
        "/documents",
        files={"file": ("notes.txt", b"same content", "text/plain")},
        data={"group_id": gid},
    )
    document_id = upload.json()["id"]
    assert upload.json()["group_id"] == gid

    r = await auth_client.post(
        f"/documents/{document_id}/replace",
        files={"file": ("notes.txt", b"same content", "text/plain")},
        data={"ungroup": "true"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["document"]["group_id"] is None


@pytest.mark.asyncio
async def test_replace_rejects_group_id_and_ungroup_together(auth_client):
    gid = (await auth_client.post("/groups", json={"name": "Conflict"})).json()["id"]
    upload = await auth_client.post(
        "/documents", files={"file": ("notes.txt", b"content", "text/plain")}
    )
    document_id = upload.json()["id"]

    r = await auth_client.post(
        f"/documents/{document_id}/replace",
        files={"file": ("notes.txt", b"changed content", "text/plain")},
        data={"group_id": gid, "ungroup": "true"},
    )

    assert r.status_code == 400


@pytest.mark.asyncio
async def test_replace_not_owned_returns_404(auth_client, client):
    files = {"file": ("a.txt", b"owner content for replace", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    import uuid as _uuid

    email = f"other-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = (
        await client.post("/auth/login", json={"email": email, "password": "password123"})
    ).json()["access_token"]
    resp = await client.post(
        f"/documents/{doc_id}/replace",
        files={"file": ("a.txt", b"malicious replace", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_processing_document_returns_409(auth_client):
    """A document is still 'pending' right after upload (the test client's
    enqueue is a no-op — see conftest.py) — deleting it now would race the
    background job's read of its file/row."""
    files = {"file": ("a.txt", b"still processing content", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    resp = await auth_client.delete(f"/documents/{doc_id}")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_not_owned_returns_404(auth_client, client):
    files = {"file": ("a.txt", b"owner content", "text/plain")}
    doc_id = (await auth_client.post("/documents", files=files)).json()["id"]

    import uuid as _uuid

    email = f"other-{_uuid.uuid4().hex}@example.com"
    await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = (
        await client.post("/auth/login", json={"email": email, "password": "password123"})
    ).json()["access_token"]
    resp = await client.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
