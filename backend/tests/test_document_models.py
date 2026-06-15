import uuid

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
