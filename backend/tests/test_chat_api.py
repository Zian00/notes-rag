"""Integration tests for POST /chat (Task 15 — Milestone E).

The graph is backed by FakeChatModel + InMemorySaver (wired in conftest.client).
Tests set fake_chat_model.responses before each request; because the graph holds
a reference to the same model object, responses are consumed at invocation time.

Scenarios:
1. Happy path — streams meta/token/citations/done; body contains a conversation_id.
2. Second turn — same conversation_id continues the thread (multi-turn persistence).
3. 401 without a token.
4. User-isolation — user B posting user A's conversation_id → 404.
"""

import json
import uuid

import pytest
from app.db.repositories.chunk import ChunkRepository
from app.db.repositories.document import DocumentRepository
from app.rag.graph.nodes import Grade
from httpx import AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot % DIM] = 1.0
    return v


async def _seed_user_doc_chunk(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Insert a document + one chunk so retrieve_notes returns real results."""
    doc = await DocumentRepository(session).create(
        user_id=user_id,
        filename="notes.pdf",
        title="Lecture Notes",
        course=None,
        content_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        storage_path="/tmp/notes.pdf",
        file_size=1,
        chunk_count=0,
        embedding_model="test",
        embedding_dimension=DIM,
    )
    await ChunkRepository(session).add_many(
        [
            {
                "document_id": doc.id,
                "user_id": user_id,
                "chunk_index": 0,
                "content": "heap is a tree-based structure",
                "embedding": _vec(0),
            }
        ]
    )
    await session.commit()


def _parse_sse_frames(body: str) -> list[tuple[str, object]]:
    """Parse an SSE body into [(event_name, parsed_data), ...] pairs."""
    frames: list[tuple[str, object]] = []
    # Frames are separated by blank lines; split on double-newline.
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data_payload: object = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_payload = json.loads(line[len("data: "):])
        if event_name is not None:
            frames.append((event_name, data_payload))
    return frames


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_happy_path(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """POST /chat streams meta → token(s) → citations → done."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    # Seed a doc+chunk so the retrieve_notes tool returns real results.
    async with maker() as s:
        # Resolve the logged-in user by checking the auth header.
        from app.core.security import TokenService

        token = auth_client.headers["Authorization"].split(" ")[1]
        user_id = TokenService().decode_access_token(token)
        await _seed_user_doc_chunk(s, user_id)

    # Script the model: agent calls retrieve_notes → grade relevant → generate answer.
    fake_chat_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "retrieve_notes", "args": {"query": "heap"}, "id": "t1"}],
        ),
        Grade(relevant=True),
        AIMessage("A heap is a tree-based structure [1]."),
    ]

    resp = await auth_client.post("/chat", json={"question": "what is a heap?"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse_frames(resp.text)
    event_names = [e for e, _ in frames]

    assert "meta" in event_names, f"Expected 'meta' frame, got: {event_names}"
    assert "token" in event_names, f"Expected 'token' frame, got: {event_names}"
    assert "citations" in event_names, f"Expected 'citations' frame, got: {event_names}"
    assert "done" in event_names, f"Expected 'done' frame, got: {event_names}"

    # meta frame must carry a parseable conversation_id.
    meta_data = next(d for e, d in frames if e == "meta")
    assert isinstance(meta_data, dict)
    convo_id = uuid.UUID(meta_data["conversation_id"])  # must not raise
    assert convo_id is not None


# ---------------------------------------------------------------------------
# 2. Second turn — same conversation_id continues the thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_second_turn_continues_thread(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """A follow-up POST with the returned conversation_id resumes the same thread."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    async with maker() as s:
        from app.core.security import TokenService

        token = auth_client.headers["Authorization"].split(" ")[1]
        user_id = TokenService().decode_access_token(token)
        await _seed_user_doc_chunk(s, user_id)

    # Turn 1 responses.
    fake_chat_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "retrieve_notes", "args": {"query": "heap"}, "id": "t1"}],
        ),
        Grade(relevant=True),
        AIMessage("A heap is a tree-based structure [1]."),
    ]

    resp1 = await auth_client.post("/chat", json={"question": "what is a heap?"})
    assert resp1.status_code == 200

    frames1 = _parse_sse_frames(resp1.text)
    meta1 = next(d for e, d in frames1 if e == "meta")
    assert isinstance(meta1, dict)
    convo_id = meta1["conversation_id"]

    # Turn 2: queue responses for the follow-up.
    # Reset _idx so the new response list is consumed from the beginning.
    fake_chat_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "retrieve_notes", "args": {"query": "min-heap"}, "id": "t2"}],
        ),
        Grade(relevant=True),
        AIMessage("A min-heap is a heap where the smallest element is at the root [1]."),
    ]
    object.__setattr__(fake_chat_model, "_idx", 0)

    resp2 = await auth_client.post(
        "/chat", json={"question": "what about a min-heap?", "conversation_id": convo_id}
    )
    assert resp2.status_code == 200

    frames2 = _parse_sse_frames(resp2.text)
    event_names2 = [e for e, _ in frames2]
    assert "meta" in event_names2
    assert "done" in event_names2

    # The returned conversation_id must be the same.
    meta2 = next(d for e, d in frames2 if e == "meta")
    assert isinstance(meta2, dict)
    assert meta2["conversation_id"] == convo_id


# ---------------------------------------------------------------------------
# 3. 401 without a token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_unauthorized(client: AsyncClient) -> None:
    """POST /chat without an Authorization header → 401."""
    resp = await client.post("/chat", json={"question": "hello"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. User isolation — user B posting user A's conversation_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_user_isolation(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """User B cannot post to user A's conversation_id; should get 404."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

    async with maker() as s:
        from app.core.security import TokenService

        token_a = auth_client.headers["Authorization"].split(" ")[1]
        user_a_id = TokenService().decode_access_token(token_a)
        await _seed_user_doc_chunk(s, user_a_id)

    # User A creates a conversation.
    fake_chat_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "retrieve_notes", "args": {"query": "heap"}, "id": "t1"}],
        ),
        Grade(relevant=True),
        AIMessage("A heap is a tree-based structure."),
    ]
    resp_a = await auth_client.post("/chat", json={"question": "what is a heap?"})
    assert resp_a.status_code == 200
    frames_a = _parse_sse_frames(resp_a.text)
    meta_a = next(d for e, d in frames_a if e == "meta")
    assert isinstance(meta_a, dict)
    convo_id_a = meta_a["conversation_id"]

    # Register + log in as user B using the same client (replace the header).
    b_email = f"userb-{uuid.uuid4().hex}@example.com"
    await auth_client.post("/auth/register", json={"email": b_email, "password": "password123"})
    resp_login = await auth_client.post(
        "/auth/login", json={"email": b_email, "password": "password123"}
    )
    token_b = resp_login.json()["access_token"]
    auth_client.headers["Authorization"] = f"Bearer {token_b}"

    # User B tries to post to user A's conversation_id → 404.
    fake_chat_model.responses = [AIMessage("should not be reached")]
    object.__setattr__(fake_chat_model, "_idx", 0)
    resp_b = await auth_client.post(
        "/chat",
        json={"question": "hi", "conversation_id": convo_id_a},
    )
    assert resp_b.status_code == 404
