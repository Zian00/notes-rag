import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.models.document import Document
from app.rag.chunking import Chunker
from app.rag.embeddings import EmbeddingsProvider
from app.rag.parsing import ParserDispatcher
from app.rag.storage import StorageBackend


class IngestionError(Exception):
    """Base class for ingestion domain errors."""


class DuplicateDocument(IngestionError):
    """Raised when the same file (by content hash) is already ingested for the user."""

    def __init__(self, existing: Document) -> None:
        # Carry the existing document so the API can answer 409 with a pointer to it.
        self.existing = existing
        super().__init__(str(existing.id))


class IngestionService:
    """Atomic ingestion: store file -> parse -> chunk -> embed -> persist. On any failure,
    rolls back the DB transaction AND deletes the stored file (compensating cleanup)."""

    # Dependencies are injected (not constructed here): tests pass fakes (fake
    # embeddings/OCR + a temp folder), production passes the real adapters — same
    # class, no edits. This is the "ports & adapters" seam in practice.
    def __init__(
        self,
        session: AsyncSession,
        documents: DocumentRepository,
        chunks: ChunkRepository,
        storage: StorageBackend,
        parser: ParserDispatcher,
        chunker: Chunker,
        embeddings: EmbeddingsProvider,
        embedding_model: str,
        embedding_dimension: int,
    ) -> None:
        self._session = session  # owns the DB transaction (commits at the end)
        self._documents = documents  # documents-table CRUD + dedup lookup
        self._chunks = chunks  # chunks-table bulk insert + vector search
        self._storage = storage  # saves/deletes the raw uploaded file
        self._parser = parser  # file bytes → text segments (per format)
        self._chunker = chunker  # text segments → token-sized chunks
        self._embeddings = embeddings  # chunk text → embedding vectors
        self._embedding_model = embedding_model  # stamped on the document (provenance)
        self._embedding_dimension = embedding_dimension  # stamped on the document (provenance)

    async def stage(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        content_type: str,
        data: bytes,
        title: str | None = None,
        course: str | None = None,
        tags: list[str] | None = None,
    ) -> Document:
        """Fast, synchronous half of ingestion — safe to call inline in the request.
        Dedup-checks, saves the raw file, and creates a 'pending' Document row with
        no chunks yet. The heavy work happens later in process(), off the request."""
        content_hash = hashlib.sha256(data).hexdigest()
        existing = await self._documents.get_by_user_and_hash(user_id, content_hash)
        if existing is not None:
            raise DuplicateDocument(existing)

        storage_path = self._storage.save(user_id, filename, data)
        try:
            document = await self._documents.create(
                user_id=user_id,
                filename=filename,
                title=title,
                course=course,
                tags=tags or [],
                content_type=content_type,
                content_hash=content_hash,
                storage_path=storage_path,
                file_size=len(data),
                page_count=None,
                chunk_count=0,
                embedding_model=self._embedding_model,
                embedding_dimension=self._embedding_dimension,
                status="pending",
            )
            await self._session.commit()
        except Exception:
            self._storage.delete(storage_path)
            raise
        return document

    async def process(self, document_id: uuid.UUID) -> None:
        """Heavy half of ingestion, run by the background worker: parse -> chunk ->
        embed -> persist chunks -> mark ready. On any failure, marks the document
        'failed' with the error message (rather than deleting it — it stays
        retryable) and re-raises so the job queue also records the failure."""
        document = await self._documents.get(document_id)
        if document is None:
            return  # deleted before processing started

        await self._documents.set_status(document.id, "processing")
        await self._session.commit()

        try:
            data = self._storage.read(document.storage_path)
            parsed = self._parser.parse(data, document.content_type)
            chunks = self._chunker.split(parsed)
            vectors = self._embeddings.embed_documents([c.content for c in chunks])

            async with self._session.begin_nested():
                await self._chunks.add_many(
                    [
                        dict(
                            document_id=document.id,
                            user_id=document.user_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            content_hash=hashlib.sha256(chunk.content.encode()).hexdigest(),
                            token_count=chunk.token_count,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            embedding=vector,
                        )
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ]
                )
                await self._documents.update_after_processing(
                    document.id,
                    page_count=parsed.page_count,
                    chunk_count=len(chunks),
                    status="ready",
                )
            await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            await self._documents.set_status(document.id, "failed", error_message=str(exc))
            await self._session.commit()
            raise
