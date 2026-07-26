import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_enqueue_processing, get_ingestion_service
from app.core.config import get_settings
from app.db.repositories.document import DocumentRepository
from app.db.session import get_db
from app.models.user import User
from app.rag.storage import LocalFileStorage
from app.schemas.document import DocumentResponse, DuplicateDocumentResponse
from app.services.ingestion import DuplicateDocument, IngestionService
from app.utils.files import sanitize_filename, sniff_content_type

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": DuplicateDocumentResponse}},
)
async def upload_document(
    file: UploadFile = File(...),  # noqa: B008
    title: str | None = Form(default=None),  # noqa: B008
    course: str | None = Form(default=None),  # noqa: B008
    tags: list[str] | None = Form(default=None),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
    service: IngestionService = Depends(get_ingestion_service),  # noqa: B008
    enqueue: Callable[[uuid.UUID], Awaitable[None]] = Depends(get_enqueue_processing),  # noqa: B008
) -> DocumentResponse | JSONResponse:
    settings = get_settings()
    data = await file.read()
    # Handler-layer validation (cheap rejects before any work): non-empty, size cap,
    # and a CONTENT SNIFF — we trust the bytes, not the client-declared content type.
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")

    content_type = sniff_content_type(file.filename or "", data)
    if content_type is None or content_type not in settings.allowed_content_types:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file type")

    try:
        document = await service.stage(
            user_id=current_user.id,
            filename=sanitize_filename(file.filename or "upload"),
            content_type=content_type,
            data=data,
            title=title,
            course=course,
            tags=tags,
        )
    except DuplicateDocument as exc:
        # Return a JSONResponse (not HTTPException) so document_id is top-level, matching
        # DuplicateDocumentResponse — HTTPException would nest it under "detail".
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Document already exists", "document_id": str(exc.existing.id)},
        )
    # Chunking/embedding is deferred to a background job — stage() only persists
    # the 'pending' document row + raw file, keeping the upload request fast.
    await enqueue(document.id)
    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    course: str | None = None,
    current_user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[DocumentResponse]:
    docs = await DocumentRepository(session).list_for_user(current_user.id, course=course)
    return [DocumentResponse.model_validate(d) for d in docs]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> Response:
    repo = DocumentRepository(session)
    # Ownership check via get_for_user: a missing OR not-yours id both yield 404 (don't
    # reveal that a document exists for another user).
    doc = await repo.get_for_user(document_id, current_user.id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    storage_path = doc.storage_path
    await repo.delete(doc)  # FK ON DELETE CASCADE removes the chunks too
    await session.commit()
    # Delete the file last. delete() takes the absolute stored path, so the storage root
    # used here doesn't matter.
    LocalFileStorage(get_settings().upload_dir).delete(storage_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
