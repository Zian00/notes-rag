import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import PasswordHasher, TokenService
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.db.repositories.refresh_token import RefreshTokenRepository
from app.db.repositories.user import UserRepository
from app.db.session import get_db
from app.models.user import User
from app.rag.chunking import Chunker
from app.rag.embeddings import EmbeddingsProvider, GeminiEmbeddingsProvider
from app.rag.ocr import OcrProvider, TesseractOcr
from app.rag.parsing import ParserDispatcher
from app.rag.storage import LocalFileStorage, StorageBackend
from app.services.auth import AuthService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService

_bearer = HTTPBearer(auto_error=False)


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:  # noqa: B008
    return AuthService(
        session,
        UserRepository(session),
        RefreshTokenRepository(session),
        PasswordHasher(),
        TokenService(),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        user_id = TokenService().decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return user


# ---------------------------------------------------------------------------
# Leaf providers — these are the seams tests override with fakes.
# Each builds one collaborator from Settings; FastAPI caches results per
# request because Depends() is re-evaluated once per dependency graph node.
# ---------------------------------------------------------------------------


def get_storage(settings: Settings = Depends(get_settings)) -> StorageBackend:  # noqa: B008
    return LocalFileStorage(settings.upload_dir)


def get_ocr(settings: Settings = Depends(get_settings)) -> OcrProvider:  # noqa: B008
    return TesseractOcr(language=settings.ocr_language, cmd=settings.tesseract_cmd)


def get_embeddings(settings: Settings = Depends(get_settings)) -> EmbeddingsProvider:  # noqa: B008
    return GeminiEmbeddingsProvider(settings)


def get_parser(
    settings: Settings = Depends(get_settings),  # noqa: B008
    ocr: OcrProvider = Depends(get_ocr),  # noqa: B008
) -> ParserDispatcher:
    return ParserDispatcher(
        ocr=ocr,
        ocr_enabled=settings.ocr_enabled,
        min_chars=settings.pdf_ocr_min_chars_per_page,
    )


def get_chunker(settings: Settings = Depends(get_settings)) -> Chunker:  # noqa: B008
    return Chunker(
        chunk_tokens=settings.chunk_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
    )


def get_ingestion_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    storage: StorageBackend = Depends(get_storage),  # noqa: B008
    parser: ParserDispatcher = Depends(get_parser),  # noqa: B008
    chunker: Chunker = Depends(get_chunker),  # noqa: B008
    embeddings: EmbeddingsProvider = Depends(get_embeddings),  # noqa: B008
) -> IngestionService:
    return IngestionService(
        session=session,
        documents=DocumentRepository(session),
        chunks=ChunkRepository(session),
        storage=storage,
        parser=parser,
        chunker=chunker,
        embeddings=embeddings,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
    )


def get_retrieval_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
    embeddings: EmbeddingsProvider = Depends(get_embeddings),  # noqa: B008
) -> RetrievalService:
    return RetrievalService(
        chunks=ChunkRepository(session),
        embeddings=embeddings,
        default_top_k=settings.retrieval_top_k,
    )
