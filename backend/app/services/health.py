import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.health import HealthResponse

logger = logging.getLogger("app")


class HealthService:
    """Business logic for the health check."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check(self) -> HealthResponse:
        database = "ok"
        try:
            await self._session.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — health must never raise
            logger.exception("Health check DB query failed")
            database = "error"
        return HealthResponse(status="ok", database=database)
