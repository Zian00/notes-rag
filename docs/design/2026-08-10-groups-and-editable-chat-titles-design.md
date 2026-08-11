# Groups + Editable Chat Titles — Design

**Date:** 2026-08-10
**Status:** Implemented (2026-08-11) — T1-T8 all shipped and closed; see §10 for the
ticket/commit mapping and §11 for where the build deviated from this draft.
**Scope:** new `groups` table + model + repository + `/groups` API; `group_id` on `conversations` and `documents`; `PATCH /conversations/{id}`; retrieval scoping change (`backend/app/rag/graph/tools.py`, `backend/app/db/repositories/chunk.py`, `backend/app/schemas/chat.py`, `backend/app/services/chat.py`); documents upload/replace metadata; Alembic migration `0008`; frontend sidebar rework (`frontend/src/components/layout/Sidebar.tsx`), documents upload UI, chat Filters panel, new API hooks. Drops the `documents.course` column.
**Continues:** turns today's free-text `course`/`tags` scoping (see the two 2026-07 design docs) into a first-class grouping entity.

---

## 1. Motivation

The app already scopes documents with a free-text `course` string and a `tags` JSONB array, and the chat Filters panel lets the user retype those same values to scope retrieval. It works but is fragile: the "group" only exists as a string the user must spell consistently in two places, there is no way to rename or delete a group, and chats have no organization at all (flat list, and titles are read-only — set once from the first question).

This design promotes grouping into a real entity. A **Group** owns both chats and documents; a chat inside a group retrieves only that group's documents, and document upload becomes group-based. Chat titles also become editable. This is the shared understanding reached by grilling on 2026-08-10.

## 2. Current state (verified against code)

- **`conversations`** (`backend/app/models/conversation.py:10-26`): `id`, `user_id`, `title` (`String(120)`, nullable, set once from first question, **no rename endpoint anywhere**), timestamps. No group field.
- **`documents`** (`backend/app/models/document.py:19-43`): `course` (`String(256)` scalar, nullable), `tags` (JSONB array, default `[]`). Set only at upload; not editable on replace.
- **`document_chunks`** (`:46-63`): filterable via join to `documents` on `course`/`tags`.
- **Retrieval filter** (`backend/app/db/repositories/chunk.py:37-66`): always `DocumentChunk.user_id == user_id`; optional `Document.course == course` and `Document.tags.contains(tags)` ("has all").
- **Retrieval flow**: `ChatRequest.course/tags` (`schemas/chat.py:10-11`) → `stream_answer` → LangGraph `config` → `retrieve_notes` tool (`rag/graph/tools.py:66-84`, LLM can also override `course`/`tags`) → `RetrievalService.search` → chunk repo.
- **Conversations API** (`api/conversations.py`): list, get, delete. No create (implicit in `POST /chat`), no PATCH.
- **Documents API** (`api/documents.py`): upload (sets course/tags via `Form`), list (optional `course` filter), replace (does NOT re-set course/tags), delete.
- **Sidebar** (`frontend/src/components/layout/Sidebar.tsx`): flat conversation list, newest-first, no sectioning, no rename UI/hook/mutation.
- **Upload UI** (`DocumentsPage.tsx`, `MetadataFields.tsx`): free-text title/course/tags.
- **Chat Filters** (`ChatInput.tsx:84-107`): free-text course/tags + numeric top_k.
- **Migrations** (`backend/app/db/migrations/versions/`): head is `0007_enable_pg_search`.

## 3. The model (settled)

A **Group** is a first-class, user-owned container.

- Owns both **chats** and **documents**. A grouped chat retrieves only its group's documents.
- Replaces the free-text `course`. Named generically ("Group") — may be used for a course, topic, or semester.
- Chats: **0-or-1** group (ungrouped allowed). Documents: **0-or-1** group (ungrouped allowed). No many-to-many.
- Groups have a **name only** in v1.

## 4. Data model

### 4.1 New `groups` table
`id` UUID PK · `user_id` UUID FK → users (cascade, indexed) · `name` `String(100)` · `created_at`/`updated_at`.
Constraint: **unique per user on `lower(name)`** (a unique index on `(user_id, lower(name))`). Name non-empty and trimmed, enforced at the API/schema layer.

### 4.2 `conversations`
Add `group_id` UUID FK → groups, **nullable**, `ON DELETE SET NULL`, indexed. (Nullable = ungrouped. SET NULL implements orphan-on-group-delete, see §6.)

### 4.3 `documents`
Add `group_id` UUID FK → groups, **nullable**, `ON DELETE SET NULL`, indexed. **Drop `course`.** Keep `tags` unchanged.

## 5. Migration `0008` (one-way, off head `0007`)

1. Create `groups` table + unique index.
2. Add `group_id` to `conversations` and `documents`.
3. **Data migration:** for each user, for each distinct **case-folded** non-empty `documents.course`, insert a group whose `name` is a representative original casing; set each document's `group_id` to the matching group. Documents with null/empty `course` → left ungrouped.
4. Drop `documents.course`.

All existing conversations start ungrouped (no signal to group them by). `tags` untouched. Downgrade re-adds `course` and back-fills from `groups.name` (best-effort).

> **As built:** step 4 was deferred out of migration `0008` — `course` was kept (nullable,
> unread) until T5 finished removing every code reference to it, then dropped in a separate
> contract migration `0009_drop_documents_course`, landed 2026-08-11 alongside a code-review
> pass over T5-T8 (not its own T-numbered ticket). See §11.

## 6. Backend API

### 6.1 Groups (`/groups` router + `GroupRepository`)
- `POST /groups` — create `{name}`. Trim; reject empty; duplicate (case-insensitive) returns the existing group rather than erroring (supports inline-create).
- `GET /groups` — list for user.
- `PATCH /groups/{id}` — rename `{name}` (same validation/uniqueness).
- `DELETE /groups/{id}` — **orphan-to-ungrouped**: the FK `ON DELETE SET NULL` nulls `group_id` on the group's chats and docs; then the group row is deleted. Never cascades content. Response reports counts for the confirm UI, e.g. `{chats_ungrouped: N, documents_ungrouped: M}`.

### 6.2 Conversations
- `PATCH /conversations/{id}` — ownership-checked; updates `title` (rename) and/or `group_id` (move to group / ungroup). Validates the target group belongs to the user.

### 6.3 Chat request + retrieval scoping (server-enforced)
- `ChatRequest`: **remove `course`**; add optional `group_id` used **only when creating a new conversation** (the "new chat within a group section" case). Keep `tags`.
- `stream_answer`: on new-conversation creation, persist `group_id`. On every turn, read the group from the **stored `conversation.group_id`** (not the client) and pass it into `config`.
- `retrieve_notes` tool: **remove the `course` param** so the LLM cannot widen scope; group is always applied from config.
- `ChunkRepository._base_chunk_query`: replace the `course ==` branch with a `Document.group_id == group_id` branch when a group is set. Ungrouped chat (`group_id is None`) applies no group filter (searches all user docs). `tags` filter unchanged.

### 6.4 Documents
- Upload: accept optional `group_id` (replaces the `course` form field). Keep `tags`.
- Make group (and tags) **editable after upload**, including on **replace** (currently metadata can't change).
- List: optional `group_id` filter (replaces `course` query param).

## 7. Frontend (all group UX inline in the sidebar)

- **Sidebar**: chats rendered under collapsible **group sections** + an **Ungrouped** section. A "＋ New group" action; each section header has a "⋯" menu → **Rename** / **Delete** (delete shows a confirm dialog spelling out the orphan counts).
- **New chat**: started from within a group section pre-assigns that group (first `/chat` call carries `group_id`); the top-level "New chat" is ungrouped.
- **Chat row menu**: **Rename** (inline edit of the title) · **Move to group** · **Delete**.
- **Upload / Documents page**: group **dropdown** (existing groups + inline "New group" + "None") replacing the free-text course input; tags input stays; group editable on existing docs.
- **Chat Filters panel**: drop the free-text **course** input; keep tags + top_k.
- **New API hooks**: groups list/create/rename/delete; conversation rename/move (`PATCH`); document group update.

## 8. Non-goals (v1)

Auto-generated chat titles; many-to-many membership; group color/description/icon; group-level tags; a grouped chat searching across multiple groups or including ungrouped docs; a separate group-management page.

## 9. Risks / notes

- **Dropping `course` is irreversible forward** — the migration must correctly fold case variants and assign before the drop. Covered by a migration test on representative data. (As built: the drop itself was pushed to a later migration once T5 confirmed the column was fully dead — see §5's "As built" note — so this risk applied to `0009`, not `0008`.)
- **Server-enforced scope** means the LLM tool loses a capability (`course` arg); prompts referencing it must be updated.
- **Moving a chat's group** changes *future*-turn retrieval only; already-persisted messages/history are untouched (documented behavior, not a bug).
- **Unique constraint** uses `lower(name)`; inline-create-duplicate resolves to the existing group with a toast rather than a hard error.

## 10. Delivery plan — tickets (blockers-first)

Tracer-bullet slices; build order follows the dependency edges. **All eight shipped and closed**
(2026-08-10 – 2026-08-11); each row links its issue and landing commit.

- **T1 — Group data model + migration** *(blocks all)* — [#1](https://github.com/Zian00/notes-rag/issues/1), `91facdd`: `groups` table, `Group` model, `group_id` FKs on conversations & documents, `GroupRepository` CRUD, Alembic `0008` (create + migrate `course`→groups; the drop itself deferred, see §5). Migration test.
- **T2 — Groups API** *(needs T1)* — [#2](https://github.com/Zian00/notes-rag/issues/2), `d2a9ab3`: `/groups` create/list/rename/delete with orphan-to-ungrouped + counts.
- **T3 — Conversation rename + move** *(needs T1)* — [#3](https://github.com/Zian00/notes-rag/issues/3), `e2dc975`: `PATCH /conversations/{id}` (title + group_id), ownership + group-ownership validation.
- **T4 — Group-scoped retrieval** *(needs T1)* — [#4](https://github.com/Zian00/notes-rag/issues/4), `666d3ac`: derive group from stored conversation; chunk-query group filter; remove `course` from `ChatRequest` + `retrieve_notes`; new-chat `group_id` plumb-through; tags unchanged.
- **T5 — Document group assignment** *(needs T1, T2)* — [#5](https://github.com/Zian00/notes-rag/issues/5), `2ebc230`: upload accepts `group_id`; group/tags editable post-upload and on replace; list filter by group.
- **T6 — Sidebar groups + CRUD (frontend)** *(needs T2, T3)* — [#6](https://github.com/Zian00/notes-rag/issues/6), `132519c`: collapsible group sections + Ungrouped, New group, section ⋯ rename/delete-with-confirm, new-chat-within-section pre-assign. Also removed the now-dead free-text `course` wiring from Chat Filters/Documents UI as a compile-fix (see §11) — T8 later replaced it with the real group picker.
- **T7 — Chat rename + move UI (frontend)** *(needs T3, T6)* — [#7](https://github.com/Zian00/notes-rag/issues/7), `51d6e91`: inline rename, move-to-group in the row menu.
- **T8 — Upload/Filters UI (frontend)** *(needs T5)* — [#8](https://github.com/Zian00/notes-rag/issues/8), `73f775c`: group dropdown with inline-create on the Documents page; the course field was already gone from Filters as of T6.

Order actually followed: **T1 → T5 → {T6 → T7 → T8}** in one continuous session (2026-08-10 into
2026-08-11), rather than the {T2,T3,T4} parallel-fan-out this section originally sketched — T2-T4
still landed in the sketched dependency order relative to T1, just sequentially by a single
implementer rather than in parallel.

## 11. Implementation notes (added 2026-08-11)

Places the actual build deviated from or extended this draft:

- **`course` drop deferred to migration `0009`** (`0009_drop_documents_course`, landed alongside
  the code-review pass below) rather than happening inside `0008` as §5 originally specified —
  `0008`'s own migration file documents this explicitly as an intentional expand/contract split,
  keeping `course` readable until T5 finished migrating every code reference off it.
- **A code-review pass after T5-T8** (Standards + Spec two-axis review against commit `666d3ac`,
  the pre-T5 baseline) found and fixed four gaps beyond the original ticket scope:
  - `list_documents` (§6.4) was missing the same group-ownership validation upload/PATCH already
    had — a foreign/nonexistent `group_id` silently returned `[]` instead of 404ing. Fixed to
    match.
  - `/replace` (§6.4 "editable... including on replace") could set a document's group but not
    *clear* one, since multipart form fields can't express "explicit null" — added an explicit
    `ungroup` flag.
  - Two independently-defined `UNSET` partial-update sentinels (one in each of
    `db/repositories/document.py` and `db/repositories/conversation.py`) were consolidated into
    a shared `app/db/sentinels.py`.
  - `Sidebar.tsx` (§7) had grown to ~570 lines covering three unrelated concerns; split into
    `components/layout/sidebar/{SectionHeader,GroupSection,UngroupedSection,ConversationItem}.tsx`.
    A `useInlineEdit` hook also replaced 4x duplicated inline-rename/create form logic across the
    Sidebar and `GroupSelect`.
- **T8's actual group-picker UI (`GroupSelect`) is a native `<select>`, not a Radix dropdown** —
  an early attempt at a nested `DropdownMenuSub` for "move to group" proved fragile to drive with
  `userEvent` in jsdom tests, so both `GroupSelect` and `ConversationItem`'s "Move to" list use
  plain `<select>`/flat `DropdownMenuItem` lists instead of nested menus.
