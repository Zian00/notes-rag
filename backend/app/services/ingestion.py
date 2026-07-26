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

    async def stage_replace(self, document_id: uuid.UUID, data: bytes) -> tuple[Document, bool]:
        """Fast half of Replace: hash the new file first. If it matches the
        document's CURRENT hash, short-circuit — no work at all. Otherwise, save
        the new file bytes under a NEW storage path (the old file/chunks are left
        completely alone until process_replace succeeds) and mark 'processing'."""
        document = await self._documents.get(document_id)
        if document is None:
            raise IngestionError(f"Document {document_id} not found")

        new_hash = hashlib.sha256(data).hexdigest()
        if new_hash == document.content_hash:
            return document, True

        new_storage_path = self._storage.save(document.user_id, document.filename, data)
        await self._documents.set_status(document.id, "processing")
        await self._session.commit()
        # Stamp the new identity now so the caller/enqueue step has what it needs
        # to pass to process_replace — but note the DB row's content_hash/chunk_count
        # are NOT updated here; that only happens once process_replace succeeds.
        document.storage_path = new_storage_path
        document.content_hash = new_hash
        document.file_size = len(data)
        return document, False

    async def process_replace(
        self,
        document_id: uuid.UUID,
        new_storage_path: str,
        new_content_hash: str,
        new_file_size: int,
    ) -> None:
        """Heavy half of Replace: parse+chunk the new file, diff its chunk hashes
        against the document's EXISTING chunks (fetched via get_hashes_for_document,
        keyed on the OLD content, since the DB row's own content_hash/storage_path
        haven't been overwritten yet), reuse what's unchanged, embed only what's
        new, delete what's gone, then atomically flip the document over to the
        new version. On failure, the document is restored to 'ready' with its
        OLD identity untouched — it never actually lost anything."""
        document = await self._documents.get(document_id)
        if document is None:
            # Deleted between stage_replace and process_replace: the new file
            # stage_replace already wrote is now orphaned — clean it up.
            self._storage.delete(new_storage_path)
            return

        # IMPORTANT: `.get()` alone can return the SAME in-memory object that
        # stage_replace already stamped with the NEW storage_path/content_hash
        # (SQLAlchemy's identity map + expire_on_commit=False means the object
        # isn't invalidated just because stage_replace committed a DIFFERENT
        # field). If we shared a session with stage_replace, `document.storage_path`
        # here would silently be the NEW path, not the OLD one — a fresh SELECT
        # via refresh() forces the object's attributes back to the DB's actual
        # committed row before we read anything off it.
        await self._session.refresh(document)

        old_hash_to_id = await self._chunks.get_hashes_for_document(document.id)
        old_storage_path = document.storage_path  # the path BEFORE this replace (still on disk)

        try:
            data = self._storage.read(new_storage_path)
            parsed = self._parser.parse(data, document.content_type)
            new_chunks = self._chunker.split(parsed)
            new_hashes = [hashlib.sha256(c.content.encode()).hexdigest() for c in new_chunks]

            to_embed_indices = [i for i, h in enumerate(new_hashes) if h not in old_hash_to_id]
            vectors = self._embeddings.embed_documents(
                [new_chunks[i].content for i in to_embed_indices]
            )
            vector_by_index = dict(zip(to_embed_indices, vectors, strict=True))

            async with self._session.begin_nested():
                new_rows = []
                for i, (chunk, content_hash) in enumerate(zip(new_chunks, new_hashes, strict=True)):
                    if content_hash in old_hash_to_id:
                        # Unchanged content — keep the existing row, just reposition it.
                        await self._chunks.update_chunk_position(
                            old_hash_to_id[content_hash],
                            chunk_index=chunk.chunk_index,
                            page_number=chunk.page_number,
                            section=chunk.section,
                        )
                    else:
                        new_rows.append(
                            dict(
                                document_id=document.id,
                                user_id=document.user_id,
                                chunk_index=chunk.chunk_index,
                                content=chunk.content,
                                content_hash=content_hash,
                                token_count=chunk.token_count,
                                page_number=chunk.page_number,
                                section=chunk.section,
                                embedding=vector_by_index[i],
                            )
                        )
                if new_rows:
                    await self._chunks.add_many(new_rows)

                # Any old chunk whose hash isn't in the new set is gone from the document.
                stale_ids = [
                    chunk_id
                    for content_hash, chunk_id in old_hash_to_id.items()
                    if content_hash not in set(new_hashes)
                ]
                await self._chunks.delete_by_ids(stale_ids)

                document.storage_path = new_storage_path
                document.content_hash = new_content_hash
                document.file_size = new_file_size
                document.page_count = parsed.page_count
                document.chunk_count = len(new_chunks)
                document.status = "ready"
            await self._session.commit()
            self._storage.delete(old_storage_path)  # superseded — safe to remove now
        except Exception as exc:
            await self._session.rollback()
            # Restore the OLD identity — this replace attempt never happened, as
            # far as the document's searchable content is concerned.
            document = await self._documents.get(document_id)
            if document is not None:
                document.status = "failed"
                document.error_message = str(exc)
            self._storage.delete(new_storage_path)  # the attempted new file, never adopted
            await self._session.commit()
            raise
