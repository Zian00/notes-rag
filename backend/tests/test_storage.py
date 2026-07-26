import uuid
from pathlib import Path

from app.rag.storage import LocalFileStorage


def test_save_writes_file_under_user_dir(tmp_path: Path):
    storage = LocalFileStorage(str(tmp_path))
    user_id = uuid.uuid4()
    path = storage.save(user_id, "Lecture 3.pdf", b"data")
    p = Path(path)
    assert p.exists()
    assert p.read_bytes() == b"data"
    assert str(user_id) in path
    assert p.name.endswith("Lecture 3.pdf")


def test_delete_removes_file_and_is_idempotent(tmp_path: Path):
    storage = LocalFileStorage(str(tmp_path))
    path = storage.save(uuid.uuid4(), "x.txt", b"hi")
    storage.delete(path)
    assert not Path(path).exists()
    storage.delete(path)  # second delete must not raise


def test_read_returns_saved_bytes(tmp_path: Path):
    storage = LocalFileStorage(str(tmp_path))
    path = storage.save(uuid.uuid4(), "notes.txt", b"hello world")
    assert storage.read(path) == b"hello world"
