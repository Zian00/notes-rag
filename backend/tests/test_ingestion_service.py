import hashlib
import uuid
from pathlib import Path

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.user import UserRepository
from app.rag.chunking import Chunker
from app.rag.storage import LocalFileStorage
from app.services.ingestion import DuplicateDocument, IngestionService

from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider


async def _user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _service(db_session, storage, parser=None):
    from app.rag.parsing import ParserDispatcher

    return IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=parser or ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=30, chunk_overlap_tokens=5),
        embeddings=FakeEmbeddingsProvider(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )


@pytest.mark.asyncio
async def test_ingest_persists_document_and_chunks(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)

    data = b"first paragraph of notes. second paragraph of notes."
    doc = await svc.ingest(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=data, title="My Notes", course="BIO", tags=["midterm"],
    )

    assert doc.title == "My Notes"
    assert doc.course == "BIO"
    assert doc.tags == ["midterm"]
    assert doc.chunk_count >= 1
    assert doc.content_hash == hashlib.sha256(data).hexdigest()
    assert Path(doc.storage_path).exists()  # noqa: ASYNC240

    chunks = await ChunkRepository(db_session).list()
    assert len(chunks) == doc.chunk_count
    assert all(len(c.embedding) == 1536 for c in chunks)


@pytest.mark.asyncio
async def test_duplicate_upload_raises_and_writes_no_new_rows(db_session, tmp_path):
    user = await _user(db_session)
    svc = _service(db_session, LocalFileStorage(str(tmp_path)))
    data = b"same content here"
    await svc.ingest(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)

    before = len(await DocumentRepository(db_session).list_for_user(user.id))
    with pytest.raises(DuplicateDocument):
        await svc.ingest(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)
    after = len(await DocumentRepository(db_session).list_for_user(user.id))
    assert before == after == 1


@pytest.mark.asyncio
async def test_failure_rolls_back_and_deletes_file(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    from app.rag.parsing import ParserDispatcher

    svc = IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=30, chunk_overlap_tokens=5),
        embeddings=BoomEmbeddings(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )

    with pytest.raises(RuntimeError):
        await svc.ingest(
            user_id=user.id, filename="x.txt", content_type="text/plain", data=b"some text here"
        )

    assert await DocumentRepository(db_session).list_for_user(user.id) == []
    assert not any(Path(tmp_path).rglob("*.*"))  # noqa: ASYNC240
