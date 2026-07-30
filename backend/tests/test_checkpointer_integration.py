"""Real-Postgres checkpointer integration test (Task 17 — Milestone E).

Builds the RAG graph with a real AsyncPostgresSaver pointed at the test DB.
Runs TWO turns on ONE unique thread_id and asserts the second turn's state
contains the first turn's messages — proving genuine Postgres persistence.

Conninfo derivation:
    TEST_DATABASE_URL is the asyncpg URL (``postgresql+asyncpg://...``).
    psycopg3 needs the plain ``postgresql://...`` form, so we strip ``+asyncpg``.

Isolation:
    Each test run uses a fresh uuid4 as the thread_id.  The checkpointer's own
    tables (checkpoints, checkpoint_writes, checkpoint_blobs) are created by
    ``await saver.setup()`` (idempotent) and are NOT listed in _TRUNCATE_TABLES.
    Orphaned rows from previous runs are harmless since thread_ids are unique.

Cleanup:
    Best-effort ``await saver.adelete_thread(thread_id)`` (guarded with getattr
    in case the installed version lacks the method), then pool close.

Windows note:
    psycopg3's async driver requires a SelectorEventLoop; on Windows pytest
    defaults to ProactorEventLoop.  We use ``AsyncPostgresSaver.from_conn_string``
    (a single async connection, no pool) because AsyncConnectionPool internally
    spawns background threads that also fail on ProactorEventLoop.  A single
    connection is sufficient for the two-turn integration test.
"""

import asyncio
import selectors
import sys
import uuid
from typing import Any

import pytest
from app.core.config import Settings
from app.rag.graph import build_rag_graph
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.conftest import TEST_DATABASE_URL
from tests.fakes import FakeChatModel, FakeEmbeddingsProvider

DIM = 1536


if sys.platform == "win32":
    # psycopg3 cannot run under Windows' default ProactorEventLoop.
    # Override the pytest-asyncio event_loop_policy fixture for this module so
    # the tests here run under SelectorEventLoop, which psycopg3 supports.
    # Python 3.13 on Windows uses a DefaultEventLoopPolicy that creates a
    # ProactorEventLoop; we create a custom policy that creates a SelectorEventLoop.
    class _SelectorPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self) -> asyncio.AbstractEventLoop:
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

    @pytest.fixture()
    def event_loop_policy():  # type: ignore[misc]
        return _SelectorPolicy()


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://notes:notes@localhost:5433/notes_rag_test",
        jwt_secret="test-secret",
    )


@pytest.mark.asyncio
async def test_real_postgres_checkpointer_persists_across_turns(
    _engine: AsyncEngine,
) -> None:
    """Two turns on the same thread_id prove Postgres-backed state persistence.

    Uses AsyncPostgresSaver.from_conn_string (a single async connection, not a pool)
    to avoid the ProactorEventLoop restriction on Windows — AsyncConnectionPool spawns
    background threads that also fail under ProactorEventLoop.  A single connection is
    sufficient for this two-turn integration test.
    """
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    # Derive the psycopg3 conninfo by stripping the asyncpg driver suffix.
    # psycopg3 does not understand the SQLAlchemy "+asyncpg" marker.
    conninfo = TEST_DATABASE_URL.replace("+asyncpg", "")

    # Unique thread_id so this test run is isolated from any prior runs.
    thread_id = str(uuid.uuid4())

    model = FakeChatModel(
        responses=[
            # Turn 1: (no prior history → condense skipped) → agent → generate
            AIMessage("Hello, this is turn one."),  # agent
            AIMessage("Hello, this is turn one."),  # generate
            # Turn 2: condense (prior history exists) → agent → generate
            AIMessage("what did I say before?"),  # condense
            AIMessage("This is turn two, continuing from turn one."),  # agent
            AIMessage("This is turn two, continuing from turn one."),  # generate
        ]
    )

    # from_conn_string is an async context manager that opens a single psycopg connection.
    # It does NOT call setup() automatically, so we do it explicitly.
    # setup() is idempotent: creates checkpoint tables if they don't already exist.
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
        graph = build_rag_graph(
            chat_model=model,
            embeddings=FakeEmbeddingsProvider(DIM),
            sessionmaker=maker,
            settings=_settings(),
            checkpointer=saver,
        )

        config: dict[str, Any] = {"configurable": {"thread_id": thread_id, "user_id": uuid.uuid4()}}

        # --- Turn 1 ---
        await graph.ainvoke(
            {"messages": [HumanMessage("hi there")], "question": "hi there", "retry_count": 0},
            config,
        )

        # --- Turn 2 ---
        state2 = await graph.ainvoke(
            {
                "messages": [HumanMessage("what did I say before?")],
                "question": "what did I say before?",
                "retry_count": 0,
            },
            config,
        )

        # The second turn's accumulated state must contain turn 1's messages —
        # proving that state was persisted to Postgres and resumed correctly.
        all_msgs = state2.get("messages", [])
        human_msgs = [m for m in all_msgs if isinstance(m, HumanMessage)]
        assert len(human_msgs) >= 2, (
            f"Expected at least 2 HumanMessages across turns, got {len(human_msgs)}: "
            f"{[m.content for m in human_msgs]}"
        )
        contents = [m.content for m in human_msgs]
        assert "hi there" in contents, f"Turn-1 message missing from state: {contents}"
        assert "what did I say before?" in contents, (
            f"Turn-2 message missing from state: {contents}"
        )

        # Confirm via aget_state that Postgres holds the full history.
        persisted = await graph.aget_state(config)
        persisted_human = [
            m for m in persisted.values.get("messages", []) if isinstance(m, HumanMessage)
        ]
        assert len(persisted_human) >= 2

        # --- Cleanup ---
        # Best-effort: delete the test thread's checkpoint rows so they don't accumulate.
        # adelete_thread may not exist in every langgraph-checkpoint-postgres version.
        deleter = getattr(saver, "adelete_thread", None)
        if deleter is not None:
            try:
                await deleter(thread_id)
            except Exception:
                pass  # cleanup failure is non-fatal; orphaned rows are harmless
