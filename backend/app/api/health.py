from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.health import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(response: Response, session: AsyncSession = Depends(get_db)) -> HealthResponse:  # noqa: B008
    result = await HealthService(session).check()
    if result.database == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
