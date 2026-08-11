import hashlib
import uuid
from pathlib import Path

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.group import GroupRepository
from app.db.repositories.user import UserRepository
from app.rag.chunking import Chunker
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage
from app.services.ingestion import DocumentBusy, DuplicateDocument, IngestionService

from tests.fakes import FakeEmbeddingsProvider, FakeOcrProvider


async def _user(db_session):
    user = await UserRepository(db_session).create(
        email=f"u-{uuid.uuid4().hex}@e.com", hashed_password="x"
    )
    await db_session.commit()
    return user


def _service(db_session, storage, embeddings=None, chunk_tokens=30, chunk_overlap_tokens=5):
    return IngestionService(
        session=db_session,
        documents=DocumentRepository(db_session),
        chunks=ChunkRepository(db_session),
        storage=storage,
        parser=ParserDispatcher(FakeOcrProvider(), ocr_enabled=True, min_chars=5),
        chunker=Chunker(chunk_tokens=chunk_tokens, chunk_overlap_tokens=chunk_overlap_tokens),
        embeddings=embeddings or FakeEmbeddingsProvider(),
        embedding_model="gemini-embedding-001",
        embedding_dimension=1536,
    )


@pytest.mark.asyncio
async def test_stage_creates_pending_document_without_chunks(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)

    doc = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"some text", title="T", group_id=None, tags=["x"],
    )

    assert doc.status == "pending"
    assert doc.chunk_count == 0
    assert doc.page_count is None
    assert Path(doc.storage_path).exists()  # noqa: ASYNC240
    assert await ChunkRepository(db_session).list() == []


@pytest.mark.asyncio
async def test_stage_duplicate_raises_and_writes_no_file(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"same bytes"
    await svc.stage(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)

    before = len(await DocumentRepository(db_session).list_for_user(user.id))
    with pytest.raises(DuplicateDocument):
        await svc.stage(user_id=user.id, filename="a.txt", content_type="text/plain", data=data)
    after = len(await DocumentRepository(db_session).list_for_user(user.id))
    assert before == after == 1


@pytest.mark.asyncio
async def test_process_parses_chunks_embeds_and_marks_ready(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
    )

    await svc.process(staged.id)

    processed = await DocumentRepository(db_session).get(staged.id)
    assert processed.status == "ready"
    assert processed.chunk_count >= 1
    chunks = await ChunkRepository(db_session).list()
    assert len(chunks) == processed.chunk_count
    assert all(len(c.embedding) == 1536 for c in chunks)


@pytest.mark.asyncio
async def test_process_preserves_group_id_assigned_at_upload(db_session, tmp_path):
    user = await _user(db_session)
    group = await GroupRepository(db_session).create(user_id=user.id, name="CS101")
    await db_session.commit()
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
        group_id=group.id,
    )
    assert staged.group_id == group.id

    await svc.process(staged.id)

    processed = await DocumentRepository(db_session).get(staged.id)
    assert processed.status == "ready"
    assert processed.group_id == group.id


@pytest.mark.asyncio
async def test_process_marks_failed_and_reraises_on_embedding_error(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    svc = _service(db_session, storage, embeddings=BoomEmbeddings())
    staged = await svc.stage(
        user_id=user.id, filename="x.txt", content_type="text/plain", data=b"some text here"
    )

    with pytest.raises(RuntimeError):
        await svc.process(staged.id)

    failed = await DocumentRepository(db_session).get(staged.id)
    assert failed.status == "failed"
    assert failed.error_message == "embedding API down"
    # The staged file itself is NOT deleted on a process() failure (unlike the old
    # ingest()'s cleanup) — the document row still exists and is retryable.
    assert Path(failed.storage_path).exists()  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_process_stores_content_hash_per_chunk(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain",
        data=b"first paragraph of notes. second paragraph of notes.",
    )

    await svc.process(staged.id)

    chunks = await ChunkRepository(db_session).list()
    assert all(c.content_hash == hashlib.sha256(c.content.encode()).hexdigest() for c in chunks)


@pytest.mark.asyncio
async def test_stage_replace_short_circuits_on_identical_content(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"first paragraph of notes. second paragraph of notes."
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=data
    )
    await svc.process(staged.id)

    staged_replace = await svc.stage_replace(staged.id, data)

    assert staged_replace.no_changes is True
    assert staged_replace.document.status == "ready"  # untouched


@pytest.mark.asyncio
async def test_process_replace_reuses_unchanged_chunks_and_embeds_only_new_ones(
    db_session, tmp_path
):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    # Small chunk_tokens so the single-segment plain text splits into one chunk PER
    # sentence (rather than fitting the whole thing into a single chunk) — this is
    # what actually exercises the diff logic: "Section A content." must survive as
    # its own unchanged chunk while only the "Section B" chunk(s) differ.
    svc = _service(db_session, storage, chunk_tokens=4, chunk_overlap_tokens=0)
    original = b"Section A content. Section B content."
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=original
    )
    await svc.process(staged.id)
    original_chunks = {c.content: c.id for c in await ChunkRepository(db_session).list()}

    updated = b"Section A content. Section B CHANGED content."
    staged_replace = await svc.stage_replace(staged.id, updated)
    assert staged_replace.no_changes is False
    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )

    final_chunks = await ChunkRepository(db_session).list()
    final_contents = {c.content for c in final_chunks}
    assert "Section A content." in final_contents  # unchanged chunk kept
    assert "Section A content." in original_chunks  # and it's literally the same row
    unchanged_id = next(c.id for c in final_chunks if c.content == "Section A content.")
    assert unchanged_id == original_chunks["Section A content."]  # same row, not re-inserted


@pytest.mark.asyncio
async def test_process_replace_failure_leaves_old_document_intact(db_session, tmp_path):
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))

    class BoomEmbeddings(FakeEmbeddingsProvider):
        def embed_documents(self, texts):
            raise RuntimeError("embedding API down")

    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id,
        filename="notes.txt",
        content_type="text/plain",
        data=b"original text here",
    )
    await svc.process(staged.id)
    before_hash = staged.content_hash
    before_chunk_count = (await DocumentRepository(db_session).get(staged.id)).chunk_count

    boom_svc = _service(db_session, storage, embeddings=BoomEmbeddings())
    staged_replace = await boom_svc.stage_replace(staged.id, b"totally different new text")
    assert staged_replace.no_changes is False

    with pytest.raises(RuntimeError):
        await boom_svc.process_replace(
            staged_replace.document.id,
            staged_replace.new_storage_path,
            staged_replace.new_content_hash,
            staged_replace.new_file_size,
        )

    reloaded = await DocumentRepository(db_session).get(staged.id)
    assert reloaded.status == "failed"
    assert reloaded.content_hash == before_hash  # old version's identity preserved
    assert reloaded.chunk_count == before_chunk_count  # old chunks untouched


@pytest.mark.asyncio
async def test_process_replace_success_deletes_old_file_and_keeps_new_file(db_session, tmp_path):
    """Regression test for a file-leak bug: stage_replace and process_replace share
    one AsyncSession here (as all Replace tests do, and plausibly a real caller
    too). SQLAlchemy's identity map + expire_on_commit=False meant `.get()` inside
    process_replace could return the SAME Python object stage_replace had already
    stamped in-memory with the NEW storage_path — so `old_storage_path` captured
    the NEW path instead of the OLD one, and a successful replace deleted the
    file the document row was just committed as pointing to while leaking the
    true original file forever. Asserts the actual filesystem state after a
    successful replace: the ORIGINAL file is gone, the NEW file still exists."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=b"original text here"
    )
    await svc.process(staged.id)
    original_path = staged.storage_path
    assert Path(original_path).exists()  # noqa: ASYNC240

    staged_replace = await svc.stage_replace(staged.id, b"totally different new text")
    assert staged_replace.no_changes is False
    new_path = staged_replace.new_storage_path
    assert new_path != original_path

    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )

    assert not Path(original_path).exists()  # noqa: ASYNC240 — old file cleaned up
    reloaded = await DocumentRepository(db_session).get(staged.id)
    assert Path(reloaded.storage_path).exists()  # noqa: ASYNC240 — new file (still) on disk
    assert reloaded.storage_path == new_path


@pytest.mark.asyncio
async def test_process_replace_cleans_up_orphaned_file_when_document_deleted(db_session, tmp_path):
    """If the document is deleted between stage_replace and process_replace, the
    new file stage_replace already wrote must not be silently orphaned."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=b"original text here"
    )
    await svc.process(staged.id)

    staged_replace = await svc.stage_replace(staged.id, b"totally different new text")
    assert staged_replace.no_changes is False
    new_path = staged_replace.new_storage_path
    assert Path(new_path).exists()  # noqa: ASYNC240

    # Simulate concurrent deletion of the document row.
    doc_row = await DocumentRepository(db_session).get(staged.id)
    await DocumentRepository(db_session).delete(doc_row)
    await db_session.commit()

    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )

    assert not Path(new_path).exists()  # noqa: ASYNC240 — orphaned new file cleaned up


@pytest.mark.asyncio
async def test_process_replace_deletes_all_legacy_zombie_chunks_sharing_empty_hash(
    db_session, tmp_path
):
    """Regression test for the CRITICAL zombie-chunk bug: pre-migration-0006
    chunks all share content_hash="" (the column's server_default backfill value
    before this fix). get_hashes_for_document used to return one id PER HASH, so
    {"" : <arbitrary id>} meant Replace could only ever delete ONE of the many
    legacy chunks sharing that hash — the rest became permanent, still-searchable
    zombie rows and chunk_count silently disagreed with the real row count.
    Simulates that legacy state directly (bypassing the now-fixed migration
    backfill) and asserts Replace deletes ALL of them, not just one."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    # Small chunk_tokens so the plain text splits into several distinct chunks —
    # there must be more than one legacy row to prove "all", not just "one", get
    # cleaned up.
    svc = _service(db_session, storage, chunk_tokens=4, chunk_overlap_tokens=0)
    original = b"Alpha content here. Beta content here. Gamma content here."
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=original
    )
    await svc.process(staged.id)
    original_chunks = await ChunkRepository(db_session).list()
    assert len(original_chunks) >= 3  # sanity: more than one legacy row to zombie-ify

    # Simulate the pre-fix migration state: every existing chunk's content_hash
    # collapsed to "".
    for chunk in original_chunks:
        chunk.content_hash = ""
    await db_session.commit()

    staged_replace = await svc.stage_replace(staged.id, b"Completely different replacement text.")
    assert staged_replace.no_changes is False
    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )

    remaining_ids = {c.id for c in await ChunkRepository(db_session).list()}
    original_ids = {c.id for c in original_chunks}
    # ALL legacy rows must be gone — none survive as zombies.
    assert remaining_ids.isdisjoint(original_ids)
    reloaded = await DocumentRepository(db_session).get(staged.id)
    assert reloaded.chunk_count == len(remaining_ids)  # no silent count mismatch


@pytest.mark.asyncio
async def test_process_replace_handles_duplicate_content_hashes_correctly(db_session, tmp_path):
    """Two chunks with byte-identical content share one content_hash. Replace must
    reuse them by POPPING one old id per matching new chunk (not just matching
    the hash once) — otherwise one duplicate becomes an undeletable zombie and/or
    both new occurrences would be miscounted onto the same old row."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage, chunk_tokens=4, chunk_overlap_tokens=0)
    # Two genuinely identical sentences -> two chunks with the same content_hash.
    original = b"Same duplicated line. Same duplicated line."
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=original
    )
    await svc.process(staged.id)
    original_chunks = await ChunkRepository(db_session).list()
    duplicate_contents = [c for c in original_chunks if c.content == "Same duplicated line."]
    assert len(duplicate_contents) == 2  # sanity: genuinely two identical-content rows
    duplicate_ids = {c.id for c in duplicate_contents}

    # New version keeps ONE occurrence of the duplicate and adds new content —
    # so exactly one of the two old duplicate rows should be reused, the other
    # should be deleted as stale (not left behind as a zombie).
    updated = b"Same duplicated line. Different new line."
    staged_replace = await svc.stage_replace(staged.id, updated)
    assert staged_replace.no_changes is False
    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )

    final_chunks = await ChunkRepository(db_session).list()
    final_by_content = {}
    for c in final_chunks:
        final_by_content.setdefault(c.content, []).append(c)

    assert len(final_by_content["Same duplicated line."]) == 1  # exactly one survives
    reused_id = final_by_content["Same duplicated line."][0].id
    assert reused_id in duplicate_ids  # it's literally one of the original rows, not re-inserted
    assert "Different new line." in final_by_content  # the new content was added
    # The OTHER original duplicate id must be gone entirely — not a zombie.
    remaining_ids = {c.id for c in final_chunks}
    stale_duplicate_id = next(i for i in duplicate_ids if i != reused_id)
    assert stale_duplicate_id not in remaining_ids


@pytest.mark.asyncio
async def test_process_replace_rerun_does_not_delete_live_file(db_session, tmp_path):
    """Regression test: if process_replace is re-run with the SAME arguments as a
    just-completed successful replace (e.g. queue redelivery), old_storage_path
    now equals new_storage_path (the row was already fully swapped over on the
    first run) — an unconditional delete(old_storage_path) would delete the LIVE
    file the document row still points to."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=b"original text here"
    )
    await svc.process(staged.id)

    staged_replace = await svc.stage_replace(staged.id, b"totally different new text")
    assert staged_replace.no_changes is False
    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )
    reloaded = await DocumentRepository(db_session).get(staged.id)
    assert Path(reloaded.storage_path).exists()  # noqa: ASYNC240

    # Simulate a redelivered/duplicate job message carrying the SAME args as the
    # replace that just succeeded.
    await svc.process_replace(
        staged_replace.document.id,
        staged_replace.new_storage_path,
        staged_replace.new_content_hash,
        staged_replace.new_file_size,
    )

    reloaded_again = await DocumentRepository(db_session).get(staged.id)
    assert reloaded_again.status == "ready"
    assert reloaded_again.storage_path == reloaded.storage_path
    assert Path(reloaded_again.storage_path).exists()  # noqa: ASYNC240 — NOT deleted


@pytest.mark.asyncio
async def test_stage_replace_reprocesses_failed_document_on_same_bytes(db_session, tmp_path):
    """A document stuck in status='failed' has no other recovery path: re-uploading
    the same bytes via POST /documents 409s as a duplicate. Replace with the exact
    same bytes must NOT short-circuit as a no-op here — it's the only way to
    retry a failed document."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    data = b"original text that failed to process"
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=data
    )
    # Force it into 'failed' directly via the repository, simulating a prior
    # failed process() run (without actually running one).
    await DocumentRepository(db_session).set_status(staged.id, "failed", error_message="boom")
    await db_session.commit()

    staged_replace = await svc.stage_replace(staged.id, data)

    assert staged_replace.no_changes is False


@pytest.mark.asyncio
async def test_stage_replace_rejects_document_still_pending(db_session, tmp_path):
    """Guards against a race where an in-flight initial process() and a
    concurrently-triggered process_replace() both mutate the same document's
    chunks. A document left in status='pending' (process() never called) must
    reject Replace outright."""
    user = await _user(db_session)
    storage = LocalFileStorage(str(tmp_path))
    svc = _service(db_session, storage)
    staged = await svc.stage(
        user_id=user.id, filename="notes.txt", content_type="text/plain", data=b"some text"
    )
    assert staged.status == "pending"  # process() deliberately not called

    with pytest.raises(DocumentBusy):
        await svc.stage_replace(staged.id, b"an attempted replacement")
