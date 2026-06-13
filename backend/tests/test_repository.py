import pytest
import pytest_asyncio
from app.db.base import Base
from app.db.repositories.base import BaseRepository
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column


class _Widget(Base):
    __tablename__ = "_test_widgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


@pytest_asyncio.fixture
async def widget_table(db_session):
    engine = db_session.bind
    async with engine.begin() as conn:
        await conn.run_sync(_Widget.__table__.create, checkfirst=True)
    yield
    # Release any locks the test's session still holds (e.g. an open
    # SELECT transaction) before dropping the table, or DROP deadlocks.
    await db_session.rollback()
    async with engine.begin() as conn:
        await conn.run_sync(_Widget.__table__.drop, checkfirst=True)


@pytest.mark.asyncio
async def test_create_and_get(db_session, widget_table):
    repo = BaseRepository(_Widget, db_session)

    created = await repo.create(name="alpha")
    await db_session.commit()

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert fetched.name == "alpha"


@pytest.mark.asyncio
async def test_get_missing_returns_none(db_session, widget_table):
    repo = BaseRepository(_Widget, db_session)
    assert await repo.get(999999) is None
