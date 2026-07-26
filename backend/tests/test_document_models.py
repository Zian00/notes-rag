import uuid

import pytest
from app.db.repositories.user import UserRepository
from app.models.document import Document, DocumentChunk


def test_document_optional_fields_default_none():
    doc = Document(
        user_id=uuid.uuid4(),
        filename="lecture3.pdf",
        content_type="application/pdf",
        content_hash="abc",
        storage_path="/tmp/x",
        file_size=10,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )
    assert doc.filename == "lecture3.pdf"
    assert doc.title is None
    assert doc.course is None
    assert doc.page_count is None


def test_chunk_fields():
    chunk = DocumentChunk(
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        chunk_index=0,
        content="hello",
        embedding=[0.0] * 1536,
    )
    assert chunk.chunk_index == 0
    assert chunk.content == "hello"
    assert chunk.section is None
    assert chunk.page_number is None


@pytest.mark.asyncio
async def test_document_defaults_to_pending_status_with_no_error(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    doc = Document(
        user_id=user.id,
        filename="a.txt",
        content_type="text/plain",
        content_hash="a" * 64,
        storage_path="/tmp/a.txt",
        file_size=1,
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )
    db_session.add(doc)
    await db_session.flush()
    assert doc.status == "pending"
    assert doc.error_message is None
