# Chat Attachment History & Multi-Attach

Status: Grilled and resolved (2026-08-12)

## 1. Motivation

Currently, attaching a document from the chat composer shows a chip above the textarea. After
sending, the chip stays there (it doesn't move into the chat history). On refresh, it's gone
entirely — there's no persistent record of what was uploaded on which turn. The desired behavior
(matching ChatGPT's model): the attachment appears as part of the sent message in the chat
history, and the composer clears.

## 2. Current state

- `ChatInput` tracks a single `attachedDocumentId` — one file at a time.
- `AttachmentChip` renders above the textarea, dismissed manually or never — `submit()` doesn't
  clear it.
- `MessageResponse` has `role`, `content`, `citations` — no attachment data.
- No file-download endpoint exists on the backend.

## 3. Design decisions (resolved via grilling)

### 3.1 Backend persistence

Add `attached_document_ids: list[UUID]` to the message model (new migration). `ChatRequest` gains
an optional `attached_document_ids` field. Persisted on the user-turn message row, surfaced in
`MessageResponse`. **Display only** — retrieval stays group-scoped, the per-message list doesn't
affect which documents the RAG pipeline searches.

### 3.2 File download endpoint

New `GET /documents/{id}/download` — ownership-checked (`document.user_id == current_user.id`),
streams the raw file from local storage with correct `Content-Type`. Used by the attachment card
in chat history to open the file in a new browser tab.

### 3.3 Composer changes (multi-attach)

- File picker gains `multiple` attribute — user can select up to **5 files** per batch.
- Chips rendered in a **horizontal wrapping row**, each with independent status and dismiss.
- **Error chips** shown inline for failed uploads (filename + error + dismiss button).
- **Group-assignment popover** (T10b, ungrouped chats only): **one popover for the whole batch**,
  anchored to the chip row container — all files in the batch go to the same group.
- **Send blocked** while any chip is in `uploading`, `processing`, or `failed` state. Failed
  chips must be dismissed before Send unblocks.
- On send: `attached_document_ids` (from successful chips) passed alongside the question, then
  all chips cleared from the composer.

### 3.4 Message history rendering

- Each user message with `attached_document_ids` renders a **separate card above the text
  bubble** — one card per document, showing filename + paperclip icon.
- Card is an `<a target="_blank">` linking to `/api/documents/{id}/download`.
- If the document was later deleted (download returns 404): card shows filename **grayed out +
  "(deleted)"**, non-clickable.
- **Per-turn scoping**: each message shows only its own turn's attachments.

## 4. Non-goals

- Retrieval filtering by specific document IDs (the attachment list is display-only).
- Drag-and-drop onto the chat area (paperclip + file picker only).
- Document preview/viewer within the app (raw file opens in browser's native viewer).

## 5. Delivery plan — tickets

TBD — will be split via `/to-tickets` after this doc is committed.
