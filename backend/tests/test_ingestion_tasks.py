import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_process_document_task_delegates_to_ingestion_service_process():
    from app.jobs import ingestion_tasks

    document_id = uuid.uuid4()
    fake_process = AsyncMock()

    with patch("app.jobs.ingestion_tasks.IngestionService") as FakeService, \
         patch("app.jobs.ingestion_tasks.build_embeddings_provider"):
        FakeService.return_value.process = fake_process
        await ingestion_tasks.process_document(document_id=str(document_id))

    fake_process.assert_awaited_once_with(document_id)


@pytest.mark.asyncio
async def test_process_document_replace_task_delegates_to_ingestion_service():
    from app.jobs import ingestion_tasks

    document_id = uuid.uuid4()
    fake_process_replace = AsyncMock()

    with patch("app.jobs.ingestion_tasks.IngestionService") as FakeService, \
         patch("app.jobs.ingestion_tasks.build_embeddings_provider"):
        FakeService.return_value.process_replace = fake_process_replace
        await ingestion_tasks.process_document_replace(
            document_id=str(document_id),
            new_storage_path="/tmp/new.txt",
            new_content_hash="abc123",
            new_file_size=42,
        )

    fake_process_replace.assert_awaited_once_with(document_id, "/tmp/new.txt", "abc123", 42)
