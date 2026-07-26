import uuid

from app.core.config import get_settings
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.session import get_sessionmaker
from app.jobs.app import app
from app.rag.chunking import Chunker
from app.rag.embeddings import GeminiEmbeddingsProvider
from app.rag.semantic_chunking import SemanticChunker
from app.rag.ocr import TesseractOcr
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage
from app.services.ingestion import IngestionService


@app.task(name="process_document")
async def process_document(document_id: str) -> None:
    """Background job body: builds a real IngestionService (same adapters the API
    uses) against its own DB session, and delegates the heavy work to process().
    document_id is passed as str (job arguments must be JSON-serializable)."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        ocr = TesseractOcr(language=settings.ocr_language, cmd=settings.tesseract_cmd)
        service = IngestionService(
            session=session,
            documents=DocumentRepository(session),
            chunks=ChunkRepository(session),
            storage=LocalFileStorage(settings.upload_dir),
            parser=ParserDispatcher(
                ocr=ocr,
                ocr_enabled=settings.ocr_enabled,
                min_chars=settings.pdf_ocr_min_chars_per_page,
            ),
            chunker=Chunker(
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
                semantic_chunker=SemanticChunker(),
            ),
            embeddings=GeminiEmbeddingsProvider(settings),
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
        )
        await service.process(uuid.UUID(document_id))


@app.task(name="process_document_replace")
async def process_document_replace(
    document_id: str, new_storage_path: str, new_content_hash: str, new_file_size: int
) -> None:
    """Background job body for document replacement: mirrors process_document's
    IngestionService construction, delegating the heavy work to process_replace()."""
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        ocr = TesseractOcr(language=settings.ocr_language, cmd=settings.tesseract_cmd)
        service = IngestionService(
            session=session,
            documents=DocumentRepository(session),
            chunks=ChunkRepository(session),
            storage=LocalFileStorage(settings.upload_dir),
            parser=ParserDispatcher(
                ocr=ocr,
                ocr_enabled=settings.ocr_enabled,
                min_chars=settings.pdf_ocr_min_chars_per_page,
            ),
            chunker=Chunker(
                chunk_tokens=settings.chunk_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
                semantic_chunker=SemanticChunker(),
            ),
            embeddings=GeminiEmbeddingsProvider(settings),
            embedding_model=settings.embedding_model,
            embedding_dimension=settings.embedding_dimension,
        )
        await service.process_replace(
            uuid.UUID(document_id), new_storage_path, new_content_hash, new_file_size
        )
