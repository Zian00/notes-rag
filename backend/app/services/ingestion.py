import hashlib
import uuid
from dataclasses import dataclass

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


class DocumentBusy(IngestionError):
    """Raised when Replace is attempted on a document that's currently 'pending'
    or 'processing'. Without this guard, an in-flight initial process() and a
    concurrently-triggered process_replace() could both mutate the same
    document's chunks at once, corrupting state."""

    def __init__(self, document_id: uuid.UUID, status: str) -> None:
        self.document_id = document_id
        self.status = status
        super().__init__(
            f"Cannot replace document {document_id}: still {status!r} — try again once it finishes."
        )


@dataclass(frozen=True)
class StagedReplace:
    """Value object returned by stage_replace. Carries the NEW file's identity
    without mutating the persistent `document` ORM object's own attributes —
    the old approach (stamping document.storage_path/content_hash/file_size
    in-memory, unflushed) was fragile: any future code path that triggers an
    autoflush/commit between stage_replace and process_replace would leak
    those values into the DB prematurely, corrupting what process_replace reads
    back as the "old" identity."""

    document: Document
    new_storage_path: str
    new_content_hash: str
    new_file_size: int
    no_changes: bool


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
        group_id: uuid.UUID | None = None,
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
                group_id=group_id,
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

    async def stage_replace(self, document_id: uuid.UUID, data: bytes) -> StagedReplace:
        """Fast half of Replace: hash the new file first. If it matches the
        document's CURRENT hash AND the document isn't stuck 'failed', short-circuit
        — no work at all. Otherwise, save the new file bytes under a NEW storage
        path (the old file/chunks are left completely alone until process_replace
        succeeds) and mark 'processing'. Raises DocumentBusy if the document is
        currently mid-flight ('pending'/'processing') to prevent two concurrent
        writers mutating the same document's chunks."""
        document = await self._documents.get(document_id)
        if document is None:
            raise IngestionError(f"Document {document_id} not found")

        if document.status not in ("ready", "failed"):
            raise DocumentBusy(document_id, document.status)

        new_hash = hashlib.sha256(data).hexdigest()
        # A document stuck 'failed' has no other recovery path: re-uploading the
        # same bytes via POST /documents 409s as a duplicate. Replace is the only
        # escape hatch, so a hash match must NOT short-circuit here — it needs to
        # actually be reprocessed (treated as if it were a genuine change).
        if new_hash == document.content_hash and document.status != "failed":
            return StagedReplace(
                document=document,
                new_storage_path=document.storage_path,
                new_content_hash=document.content_hash,
                new_file_size=document.file_size,
                no_changes=True,
            )

        new_storage_path = self._storage.save(document.user_id, document.filename, data)
        await self._documents.set_status(document.id, "processing")
        await self._session.commit()
        # NOTE: document's own attributes are deliberately left untouched — the
        # new identity lives only in the StagedReplace value object below. See
        # StagedReplace's docstring for why in-place mutation was fragile.
        return StagedReplace(
            document=document,
            new_storage_path=new_storage_path,
            new_content_hash=new_hash,
            new_file_size=len(data),
            no_changes=False,
        )

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

        # NOTE: stage_replace no longer mutates the Document ORM object's own
        # attributes in-memory (see StagedReplace) — the new identity now lives
        # only in the value object it returns, so `.get()`'s identity-mapped
        # object should already reflect the DB's actual committed row. This
        # refresh() is kept anyway as defense-in-depth: it's cheap, and it
        # protects against any OTHER future code path stamping stale attributes
        # onto this same in-memory object before we read old_storage_path below.
        await self._session.refresh(document)

        # old_hash_to_ids maps content_hash -> ALL row ids sharing that hash (not
        # just one) — see get_hashes_for_document's docstring for why: legacy
        # documents can have every chunk collapsed onto hash "", and any document
        # can have genuine byte-identical duplicate chunks. We pop ids off these
        # lists as new chunks consume them below, so whatever's left unconsumed
        # after the loop is stale-by-construction, not by fragile hash-set math.
        old_hash_to_ids = await self._chunks.get_hashes_for_document(document.id)
        old_storage_path = document.storage_path  # the path BEFORE this replace (still on disk)

        try:
            data = self._storage.read(new_storage_path)
            parsed = self._parser.parse(data, document.content_type)
            new_chunks = self._chunker.split(parsed)
            new_hashes = [hashlib.sha256(c.content.encode()).hexdigest() for c in new_chunks]

            # Work on a local copy so we can pop ids as they're claimed by a new
            # chunk, in encounter order — this is what makes N identical new
            # chunks correctly reuse up to N identical old rows (and no more).
            remaining_old_ids = {h: list(ids) for h, ids in old_hash_to_ids.items()}
            reused_chunk_id_by_index: dict[int, uuid.UUID] = {}
            for i, h in enumerate(new_hashes):
                available = remaining_old_ids.get(h)
                if available:
                    reused_chunk_id_by_index[i] = available.pop(0)

            to_embed_indices = [
                i for i in range(len(new_hashes)) if i not in reused_chunk_id_by_index
            ]
            vectors = self._embeddings.embed_documents(
                [new_chunks[i].content for i in to_embed_indices]
            )
            vector_by_index = dict(zip(to_embed_indices, vectors, strict=True))

            async with self._session.begin_nested():
                new_rows = []
                for i, (chunk, content_hash) in enumerate(zip(new_chunks, new_hashes, strict=True)):
                    reused_id = reused_chunk_id_by_index.get(i)
                    if reused_id is not None:
                        # Unchanged content — keep the existing row, just reposition it.
                        await self._chunks.update_chunk_position(
                            reused_id,
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

                # Anything left in remaining_old_ids after every new chunk has
                # claimed its match is genuinely gone from the document — delete
                # ALL of it, not just one id per hash.
                stale_ids = [
                    chunk_id for ids in remaining_old_ids.values() for chunk_id in ids
                ]
                await self._chunks.delete_by_ids(stale_ids)

                document.storage_path = new_storage_path
                document.content_hash = new_content_hash
                document.file_size = new_file_size
                document.page_count = parsed.page_count
                document.chunk_count = len(new_chunks)
                document.status = "ready"
            await self._session.commit()
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

        # Only reached on success. Deleting the old file here (outside try/except)
        # means a failure in delete() itself can never be mistaken for a replace
        # failure and trigger deletion of new_storage_path (the file the document
        # row was just successfully committed to point at). Guarded on inequality
        # because a redelivered/duplicate job message for an already-completed
        # replace would otherwise have old_storage_path == new_storage_path (the
        # row was already fully swapped over) and delete the live file.
        if old_storage_path != new_storage_path:
            self._storage.delete(old_storage_path)
