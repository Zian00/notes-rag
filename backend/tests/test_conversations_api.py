"""Integration tests for conversations endpoints (Task 16 — Milestone E).

GET /conversations, GET /conversations/{id}, DELETE /conversations/{id}.

Scenarios:
1. After a chat turn, GET /conversations lists the conversation (newest-first).
2. GET /conversations/{id} returns message history (user + assistant messages).
3. DELETE /conversations/{id} → 204; subsequent GET → 404.
4. User isolation: user B cannot GET/DELETE user A's conversation → 404.
5. 401 without a token for each endpoint.
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

from tests.conftest import hash_content

DIM = 1536


def _vec(slot: int) -> list[float]:
    v = [0.0] * DIM
    v[slot % DIM] = 1.0
    return v


async def _seed_doc(session: AsyncSession, user_id: uuid.UUID) -> None:
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
                "content_hash": hash_content("heap is a tree-based structure"),
                "embedding": _vec(0),
            }
        ]
    )
    await session.commit()


def _parse_meta(body: str) -> dict:
    """Extract the meta frame's data dict from an SSE body."""
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event_name == "meta" and isinstance(data, dict):
            return data
    raise AssertionError("No 'meta' frame found in SSE body")


async def _do_chat(
    ac: AsyncClient,
    fake_chat_model,
    question: str,
    *,
    conversation_id: str | None = None,
) -> str:
    """Helper: POST /chat and return the conversation_id from the meta frame."""
    fake_chat_model.responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "retrieve_notes", "args": {"query": "heap"}, "id": "t1"}],
        ),
        Grade(relevant=True, reason="context answers the question"),
        AIMessage("A heap is a tree-based structure [1]."),
    ]
    body: dict = {"question": question}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    resp = await ac.post("/chat", json=body)
    assert resp.status_code == 200, resp.text
    meta = _parse_meta(resp.text)
    return meta["conversation_id"]


# ---------------------------------------------------------------------------
# 1. GET /conversations lists the conversation after a chat turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """After a POST /chat, GET /conversations includes the new conversation."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        from app.core.security import TokenService

        token = auth_client.headers["Authorization"].split(" ")[1]
        user_id = TokenService().decode_access_token(token)
        await _seed_doc(s, user_id)

    convo_id = await _do_chat(auth_client, fake_chat_model, "what is a heap?")

    resp = await auth_client.get("/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    ids = [c["id"] for c in data]
    assert convo_id in ids


# ---------------------------------------------------------------------------
# 2. GET /conversations/{id} returns message history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation_detail(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """GET /conversations/{id} returns the conversation + user+assistant messages."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        from app.core.security import TokenService

        token = auth_client.headers["Authorization"].split(" ")[1]
        user_id = TokenService().decode_access_token(token)
        await _seed_doc(s, user_id)

    convo_id = await _do_chat(auth_client, fake_chat_model, "what is a heap?")

    resp = await auth_client.get(f"/conversations/{convo_id}")
    assert resp.status_code == 200
    detail = resp.json()

    assert detail["id"] == convo_id
    assert "messages" in detail
    roles = [m["role"] for m in detail["messages"]]
    assert "user" in roles
    assert "assistant" in roles


# ---------------------------------------------------------------------------
# 3. DELETE /conversations/{id} → 204; then GET → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """DELETE removes the conversation; subsequent GET returns 404."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        from app.core.security import TokenService

        token = auth_client.headers["Authorization"].split(" ")[1]
        user_id = TokenService().decode_access_token(token)
        await _seed_doc(s, user_id)

    convo_id = await _do_chat(auth_client, fake_chat_model, "what is a heap?")

    del_resp = await auth_client.delete(f"/conversations/{convo_id}")
    assert del_resp.status_code == 204

    get_resp = await auth_client.get(f"/conversations/{convo_id}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. User isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversations_user_isolation(
    auth_client: AsyncClient,
    fake_chat_model,
    _engine: AsyncEngine,
) -> None:
    """User B cannot GET or DELETE user A's conversation (404)."""
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        from app.core.security import TokenService

        token_a = auth_client.headers["Authorization"].split(" ")[1]
        user_a_id = TokenService().decode_access_token(token_a)
        await _seed_doc(s, user_a_id)

    # User A creates a conversation.
    convo_id_a = await _do_chat(auth_client, fake_chat_model, "what is a heap?")

    # Register + log in as user B.
    b_email = f"isolation-b-{uuid.uuid4().hex}@example.com"
    await auth_client.post("/auth/register", json={"email": b_email, "password": "password123"})
    resp_login = await auth_client.post(
        "/auth/login", json={"email": b_email, "password": "password123"}
    )
    token_b = resp_login.json()["access_token"]
    auth_client.headers["Authorization"] = f"Bearer {token_b}"

    # B tries to GET A's conversation → 404.
    get_resp = await auth_client.get(f"/conversations/{convo_id_a}")
    assert get_resp.status_code == 404

    # B tries to DELETE A's conversation → 404.
    del_resp = await auth_client.delete(f"/conversations/{convo_id_a}")
    assert del_resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. 401 without a token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversations_unauthorized(client: AsyncClient) -> None:
    """All conversations endpoints require authentication."""
    fake_id = str(uuid.uuid4())

    resp = await client.get("/conversations")
    assert resp.status_code == 401

    resp = await client.get(f"/conversations/{fake_id}")
    assert resp.status_code == 401

    resp = await client.delete(f"/conversations/{fake_id}")
    assert resp.status_code == 401
