import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.utils.files import sanitize_filename


class StorageBackend(ABC):
    """Port: persist and remove the original uploaded file."""

    @abstractmethod
    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str: ...

    @abstractmethod
    def delete(self, path: str) -> None: ...


class LocalFileStorage(StorageBackend):
    """Writes files under UPLOAD_DIR/{user_id}/{uuid}_{safe_name}."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def save(self, user_id: uuid.UUID, filename: str, data: bytes) -> str:
        safe = sanitize_filename(filename)
        user_dir = self._root / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = user_dir / f"{uuid.uuid4().hex}_{safe}"
        dest.write_bytes(data)
        return str(dest)

    def delete(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)
