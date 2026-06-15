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

    async def ingest(
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
        # Idempotent ingestion: the same bytes for the same user are never embedded
        # twice. Checked before any work, so a duplicate writes no file and no rows.
        content_hash = hashlib.sha256(data).hexdigest()
        existing = await self._documents.get_by_user_and_hash(user_id, content_hash)
        if existing is not None:
            raise DuplicateDocument(existing)

        # The file is written before the DB transaction, so the failure path below must
        # delete it explicitly — the DB can roll back, the filesystem cannot.
        storage_path = self._storage.save(user_id, filename, data)
        try:
            # Savepoint = all-or-nothing for the document + its chunks. A savepoint
            # (rather than a full session rollback) keeps the failure scoped to this
            # ingestion and avoids expiring unrelated objects loaded in the session.
            async with self._session.begin_nested():
                parsed = self._parser.parse(data, content_type)  # bytes → text segments
                chunks = self._chunker.split(parsed)  # segments → chunks
                vectors = self._embeddings.embed_documents(
                    [c.content for c in chunks]
                )  # chunks → embedding vectors

                document = await self._documents.create(  # one documents row
                    user_id=user_id,
                    filename=filename,
                    title=title,
                    course=course,
                    tags=tags or [],
                    content_type=content_type,
                    content_hash=content_hash,
                    storage_path=storage_path,
                    file_size=len(data),
                    page_count=parsed.page_count,
                    chunk_count=len(chunks),
                    embedding_model=self._embedding_model,
                    embedding_dimension=self._embedding_dimension,
                )
                await self._chunks.add_many(  # one row per chunk
                    [
                        dict(
                            document_id=document.id,
                            user_id=user_id,
                            chunk_index=chunk.chunk_index,
                            content=chunk.content,
                            token_count=chunk.token_count,
                            page_number=chunk.page_number,
                            section=chunk.section,
                            embedding=vector,
                        )
                        # strict=True fails loudly if chunk/vector counts ever diverge,
                        # rather than silently dropping data.
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ]
                )
        except Exception:
            # Compensating cleanup: the savepoint rolls back the rows automatically,
            # but the already-written file must be removed by hand.
            self._storage.delete(storage_path)
            raise
        await self._session.commit()
        return document
