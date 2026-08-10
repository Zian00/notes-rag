"""Integration tests for the groups endpoints (T2).

POST /groups, GET /groups, PATCH /groups/{id}, DELETE /groups/{id}.

Covers: create, case-insensitive duplicate-resolves-to-existing, list scoping,
rename, rename name-conflict (409), not-found/isolation (404), delete
orphan-to-ungrouped with counts, and 401 without a token.
"""

import uuid

import pytest
from app.core.security import TokenService
from app.db.repositories.conversation import ConversationRepository
from app.db.repositories.document import DocumentRepository
from app.models.document import Document
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

DIM = 1536


def _user_id(ac: AsyncClient) -> uuid.UUID:
    token = ac.headers["Authorization"].split(" ")[1]
    return TokenService().decode_access_token(token)


@pytest.mark.asyncio
async def test_create_and_list(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/groups", json={"name": "CS101"})
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["name"] == "CS101"
    assert created["id"]

    resp = await auth_client.get("/groups")
    assert resp.status_code == 200
    names = [g["name"] for g in resp.json()]
    assert "CS101" in names


@pytest.mark.asyncio
async def test_create_duplicate_case_insensitive_returns_existing(auth_client: AsyncClient) -> None:
    first = (await auth_client.post("/groups", json={"name": "CS101"})).json()
    dup = await auth_client.post("/groups", json={"name": "  cs101 "})
    assert dup.status_code == 200
    # Same group, not a second row.
    assert dup.json()["id"] == first["id"]
    listing = (await auth_client.get("/groups")).json()
    assert sum(1 for g in listing if g["id"] == first["id"]) == 1


@pytest.mark.asyncio
async def test_blank_name_rejected(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/groups", json={"name": "   "})
    assert resp.status_code == 422  # StringConstraints strips then fails min_length


@pytest.mark.asyncio
async def test_rename(auth_client: AsyncClient) -> None:
    gid = (await auth_client.post("/groups", json={"name": "old"})).json()["id"]
    resp = await auth_client.patch(f"/groups/{gid}", json={"name": "new"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "new"


@pytest.mark.asyncio
async def test_rename_conflict_returns_409(auth_client: AsyncClient) -> None:
    await auth_client.post("/groups", json={"name": "Math"})
    gid = (await auth_client.post("/groups", json={"name": "Physics"})).json()["id"]
    # Renaming Physics → "math" collides (case-insensitively) with the existing group.
    resp = await auth_client.patch(f"/groups/{gid}", json={"name": "math"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_rename_missing_returns_404(auth_client: AsyncClient) -> None:
    resp = await auth_client.patch(f"/groups/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_orphans_chats_and_documents_with_counts(
    auth_client: AsyncClient, _engine: AsyncEngine
) -> None:
    gid = (await auth_client.post("/groups", json={"name": "CS101"})).json()["id"]
    group_uuid = uuid.UUID(gid)
    user_id = _user_id(auth_client)

    # Seed one chat and one document into the group (direct DB writes: the chat/doc
    # endpoints don't accept group_id until T4/T5).
    maker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        convo = await ConversationRepository(s).create(user_id=user_id, title="in group")
        convo.group_id = group_uuid
        doc = Document(
            user_id=user_id,
            filename="notes.pdf",
            content_type="application/pdf",
            content_hash=uuid.uuid4().hex,
            storage_path="/tmp/notes.pdf",
            file_size=1,
            embedding_model="test",
            embedding_dimension=DIM,
            group_id=group_uuid,
        )
        s.add(doc)
        await s.commit()
        convo_id, doc_id = convo.id, doc.id

    resp = await auth_client.delete(f"/groups/{gid}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"chats_ungrouped": 1, "documents_ungrouped": 1}

    # Rows survive, orphaned to ungrouped.
    async with maker() as s:
        convo = await ConversationRepository(s).get_for_user(convo_id, user_id)
        assert convo is not None and convo.group_id is None
        doc = await DocumentRepository(s).get_for_user(doc_id, user_id)
        assert doc is not None and doc.group_id is None


@pytest.mark.asyncio
async def test_user_isolation(auth_client: AsyncClient) -> None:
    """User B cannot rename or delete user A's group (404)."""
    gid = (await auth_client.post("/groups", json={"name": "A-group"})).json()["id"]

    b_email = f"iso-{uuid.uuid4().hex}@example.com"
    await auth_client.post("/auth/register", json={"email": b_email, "password": "password123"})
    token_b = (
        await auth_client.post("/auth/login", json={"email": b_email, "password": "password123"})
    ).json()["access_token"]
    auth_client.headers["Authorization"] = f"Bearer {token_b}"

    assert (await auth_client.patch(f"/groups/{gid}", json={"name": "x"})).status_code == 404
    assert (await auth_client.delete(f"/groups/{gid}")).status_code == 404
    # B's own list doesn't see A's group.
    assert (await auth_client.get("/groups")).json() == []


@pytest.mark.asyncio
async def test_groups_unauthorized(client: AsyncClient) -> None:
    fake = str(uuid.uuid4())
    assert (await client.get("/groups")).status_code == 401
    assert (await client.post("/groups", json={"name": "x"})).status_code == 401
    assert (await client.patch(f"/groups/{fake}", json={"name": "x"})).status_code == 401
    assert (await client.delete(f"/groups/{fake}")).status_code == 401
