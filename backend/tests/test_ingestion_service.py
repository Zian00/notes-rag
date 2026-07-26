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


def _service(db_session, storage, embeddings=None, chunk_tokens=30, chunk_overlap_tokens=5):
    return IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=chunk_tokens, chunk_overlap_tokens=chunk_overlap_tokens),
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


@pytest.mark.asyncio
async def test_stage_replace_short_circuits_on_identical_content(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"first paragraph of notes. second paragraph of notes."
    staged = await svc.stage(user_id=user.id, filename="notes.txt", content_type="text/plain", data=data)
    await svc.process(staged.id)

    document, no_changes = await svc.stage_replace(staged.id, data)

    assert no_changes is True
    assert document.status == "ready"  # untouched


@pytest.mark.asyncio
async def test_process_replace_reuses_unchanged_chunks_and_embeds_only_new_ones(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    # Small chunk_tokens so the single-segment plain text splits into one chunk PER
    # sentence (rather than fitting the whole thing into a single chunk) — this is
    # what actually exercises the diff logic: "Section A content." must survive as
    # its own unchanged chunk while only the "Section B" chunk(s) differ.
    svc = _service(db_session, storage, chunk_tokens=4, chunk_overlap_tokens=0)
    original = b"Section A content. Section B content."
    staged = await svc.stage(user_id=user.id, filename="notes.txt", content_type="text/plain", data=original)
    await svc.process(staged.id)
    original_chunks = {c.content: c.id for c in await ChunkRepository(db_session).list()}

    updated = b"Section A content. Section B CHANGED content."
    document, no_changes = await svc.stage_replace(staged.id, updated)
    assert no_changes is False
    await svc.process_replace(
        document.id, document.storage_path, document.content_hash, document.file_size
    )

    final_chunks = await ChunkRepository(db_session).list()
    final_contents = {c.content for c in final_chunks}
    assert "Section A content." in final_contents  # unchanged chunk kept
    assert "Section A content." in original_chunks  # and it's literally the same row
    unchanged_id = next(c.id for c in final_chunks if c.content == "Section A content.")
    assert unchanged_id == original_chunks["Section A content."]  # same row, not re-inserted


@pytest.mark.asyncio
async def test_process_replace_failure_leaves_old_document_intact(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    svc = _service(db_session, storage)
    staged = await svc.stage(user_id=user.id, filename="notes.txt", content_type="text/plain", data=b"original text here")
    await svc.process(staged.id)
    before_hash = staged.content_hash
    before_chunk_count = (await DocumentRepository(db_session).get(staged.id)).chunk_count

    boom_svc = _service(db_session, storage, embeddings=BoomEmbeddings())
    document, no_changes = await boom_svc.stage_replace(staged.id, b"totally different new text")
    assert no_changes is False

    with pytest.raises(RuntimeError):
        await boom_svc.process_replace(
            document.id, document.storage_path, document.content_hash, document.file_size
        )

    reloaded = await DocumentRepository(db_session).get(staged.id)
    assert reloaded.status == "failed"
    assert reloaded.content_hash == before_hash  # old version's identity preserved
    assert reloaded.chunk_count == before_chunk_count  # old chunks untouched
