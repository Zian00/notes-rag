import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_process_document_task_delegates_to_ingestion_service_process():
    from app.jobs import ingestion_tasks

    document_id = uuid.uuid4()
    fake_process = AsyncMock()

    with patch("app.jobs.ingestion_tasks.IngestionService") as FakeService:
        FakeService.return_value.process = fake_process
        await ingestion_tasks.process_document(document_id=str(document_id))

    fake_process.assert_awaited_once_with(document_id)
