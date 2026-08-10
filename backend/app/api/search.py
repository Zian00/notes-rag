from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, get_retrieval_service
from app.models.user import User
from app.schemas.document import ChunkMatch, SearchRequest
from app.services.retrieval import RetrievalService

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=list[ChunkMatch])
async def search(
    body: SearchRequest,
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: RetrievalService = Depends(get_retrieval_service),  # noqa: B008
) -> list[ChunkMatch]:
    results = await service.search(
        current_user.id,
        body.query,
        top_k=body.top_k,
        group_id=body.group_id,
        tags=body.tags,
    )
    # Map the repository's ChunkSearchResult dataclasses to the API ChunkMatch schema.
    return [
        ChunkMatch(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            filename=r.filename,
            title=r.title,
            content=r.content,
            page_number=r.page_number,
            section=r.section,
            score=r.score,
        )
        for r in results
    ]
