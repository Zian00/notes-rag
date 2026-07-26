import hashlib
import uuid
from pathlib import Path

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.chunking import Chunker
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage
from app.services.ingestion import DuplicateDocument, IngestionService

from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider


async def _user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _service(db_session, storage, embeddings=None):
    return IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=30, chunk_overlap_tokens=5),
        embeddings=embeddings or FakeEmbeddingsProvider(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )


@pytest.mark.asyncio
async def test_stage_creates_pending_document_without_chunks(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)

    doc = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"some text", title="T", course="BIO", tags=["x"],
    )

    assert doc.status == "pending"
    assert doc.chunk_count == 0
    assert doc.page_count is None
    assert Path(doc.storage_path).exists()  # noqa: ASYNC240
    assert await ChunkRepository(db_session).list() == []


@pytest.mark.asyncio
async def test_stage_duplicate_raises_and_writes_no_file(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"same bytes"
    await svc.stage(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)

    before = len(await DocumentRepository(db_session).list_for_user(user.id))
    with pytest.raises(DuplicateDocument):
        await svc.stage(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)
    after = len(await DocumentRepository(db_session).list_for_user(user.id))
    assert before == after == 1


@pytest.mark.asyncio
async def test_process_parses_chunks_embeds_and_marks_ready(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
    )

    await svc.process(staged.id)

    processed = await DocumentRepository(db_session).get(staged.id)
    assert processed.status == "ready"
    assert processed.chunk_count >= 1
    chunks = await ChunkRepository(db_session).list()
    assert len(chunks) == processed.chunk_count
    assert all(len(c.embedding) == 1536 for c in chunks)


@pytest.mark.asyncio
async def test_process_marks_failed_and_reraises_on_embedding_error(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    svc = _service(db_session, storage, embeddings=BoomEmbeddings())
    staged = await svc.stage(
        user_id=user.id, filename="x.txt", content_type="text/plain", data=b"some text here"
    )

    with pytest.raises(RuntimeError):
        await svc.process(staged.id)

    failed = await DocumentRepository(db_session).get(staged.id)
    assert failed.status == "failed"
    assert failed.error_message == "embedding API down"
    # The staged file itself is NOT deleted on a process() failure (unlike the old
    # ingest()'s cleanup) — the document row still exists and is retryable.
    assert Path(failed.storage_path).exists()  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_process_stores_content_hash_per_chunk(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
    )

    await svc.process(staged.id)

    chunks = await ChunkRepository(db_session).list()
    assert all(c.content_hash == hashlib.sha256(c.content.encode()).hexdigest() for c in chunks)
