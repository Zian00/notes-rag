from app.core.config import Settings


def _settings(**over: object) -> Settings:
    base = {"database_url": "postgresql+asyncpg://u:p@localhost/db", "jwt_secret": "x"}
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_phase2_defaults():
    s = _settings()
    assert s.embedding_model == "gemini-embedding-001"
    assert s.embedding_dimension == 1536
    assert s.embedding_doc_task_type == "RETRIEVAL_DOCUMENT"
    assert s.embedding_query_task_type == "RETRIEVAL_QUERY"
    assert s.chunk_tokens == 512
    assert s.chunk_overlap_tokens == 64
    assert s.upload_dir == "./uploads"
    assert s.max_upload_bytes == 26_214_400
    assert s.ocr_enabled is True
    assert s.ocr_language == "eng"
    assert s.pdf_ocr_min_chars_per_page == 10
    assert s.tesseract_cmd is None
    assert s.retrieval_top_k == 5
    assert "application/pdf" in s.allowed_content_types
    assert "image/png" in s.allowed_content_types
