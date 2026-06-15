from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # LLM (used from Phase 3)
    google_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"

    # App
    environment: Literal["development", "production"] = "development"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Embeddings (Phase 2)
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 1536
    embedding_doc_task_type: str = "RETRIEVAL_DOCUMENT"
    embedding_query_task_type: str = "RETRIEVAL_QUERY"

    # Chunking
    chunk_tokens: int = 512
    chunk_overlap_tokens: int = 64

    # Uploads / storage
    upload_dir: str = "./uploads"
    max_upload_bytes: int = 26_214_400  # 25 MiB
    allowed_content_types: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "image/png",
        "image/jpeg",
    ]

    # OCR
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    pdf_ocr_min_chars_per_page: int = 10
    tesseract_cmd: str | None = None

    # Retrieval
    retrieval_top_k: int = 5


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (one per process)."""
    return Settings()
