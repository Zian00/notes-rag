import uuid

import pytest


async def _process_synchronously(document_id: uuid.UUID, client) -> None:
    """Run IngestionService.process() inline against the same test DB/fakes the
    HTTP client uses, so an upload's chunks exist immediately (no real worker runs
    in tests — the enqueue dependency is overridden to call this instead)."""
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
    """Override for get_enqueue_processing: process the document inline instead of
    deferring to a real job queue (there's no worker running in tests)."""
    from app.api import deps

    client.app.dependency_overrides[deps.get_enqueue_processing] = (
        lambda: (lambda document_id: _process_synchronously(document_id, client))
    )


@pytest.mark.asyncio
async def test_search_returns_user_chunks(auth_client):
    _enqueue_synchronously(auth_client)
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
    _enqueue_synchronously(auth_client)
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
