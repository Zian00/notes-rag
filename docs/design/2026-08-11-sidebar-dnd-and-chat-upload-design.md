# Sidebar Drag-and-Drop, Chat-Native Upload, and Documents Redesign

**Date:** 2026-08-11
**Status:** Draft, awaiting review
**Scope:** drag-and-drop chat reordering into groups (`frontend/src/components/layout/sidebar/`), a
new file-attach affordance in `ChatInput` that uploads a document inline and auto-assigns its
group, and a redesign of `DocumentsPage` around browsing rather than uploading. New dependency:
a drag-and-drop library (`@dnd-kit/core` + `@dnd-kit/sortable`, recommended below).
**Continues:** the groups feature (`2026-08-10-groups-and-editable-chat-titles-design.md`,
T1-T8) — this reuses `group_id` scoping on both conversations and documents rather than
introducing a new concept.

---

## 1. Motivation

Three requests came out of using the T1-T8 groups feature:

1. **Moving a chat to a group today requires the row's "..." menu** ("Move to" → pick a group).
   That works, but it's a lot of clicks for what feels like it should be a drag.
2. **Uploading a document requires leaving the chat** — navigate to /documents, fill the
   upload form, come back. ChatGPT-style products let you attach a file from the chat input
   itself, with the file becoming part of that conversation's context immediately.
3. Once (2) exists, **the dedicated Documents page's current "upload-first" layout stops making
   sense** as the primary entry point — most uploads will happen from chat, so Documents becomes
   more of a library/management view.

This design covers all three, in the order they depend on each other: drag-and-drop is
independent; chat-upload and the Documents redesign are sequenced (the redesign is *driven by*
chat-upload existing).

## 2. Current state (verified against code)

- **Sidebar** (`frontend/src/components/layout/sidebar/`): `GroupSection`/`UngroupedSection`
  render `ConversationItem` rows. Moving a chat between groups is `ConversationItem`'s "..." →
  "Move to" list, calling `useUpdateConversation` (`PATCH /conversations/{id}`). No drag
  affordance, no drag library in `package.json`.
- **Chat input** (`frontend/src/components/chat/ChatInput.tsx`): a `Textarea` + Send/Stop button
  + a collapsible Filters panel (tags, top_k). No file input, no attach affordance.
- **Upload endpoint** (`POST /documents`, `backend/app/api/documents.py`): multipart form
  (`file`, `title?`, `group_id?`, `tags?`), returns a `DocumentResponse` immediately with
  `status: "pending"`; chunking/embedding happens in a background job (`enqueue_document_processing`).
  A document only becomes retrievable (`retrieve_notes` reads `document_chunks`, which only exist
  once a document reaches `status: "ready"`) after that job finishes — this is unchanged and load-
  bearing for chat-upload's design below.
- **Documents page** (`frontend/src/routes/DocumentsPage.tsx`): upload form (`UploadDropzone` +
  `MetadataFields` incl. `GroupSelect`) at the top, document list (`DocumentList` → `DocumentRow`)
  below. No group filter on the page itself — `DocumentList` always calls `useDocuments()`
  unfiltered.
- **`useChat`** (`frontend/src/api/hooks/useChat.ts`): `send(question, filters?)` posts to
  `/chat` with `group_id` honored only when creating a new conversation (see T4). A chat's group
  is otherwise fixed at creation and read from the stored conversation server-side.

## 3. Drag-and-drop (Sidebar)

### 3.1 Library

**`@dnd-kit/core` + `@dnd-kit/sortable`** — the modern, actively-maintained choice for React 18/19;
built-in keyboard support (arrow keys + space/enter to pick up/drop) and pointer+touch sensors out
of the box, which satisfies "full library-based DnD" without hand-rolling accessibility. (Rejected:
`react-dnd` — heavier API, HTML5-backend-only touch support needs a second backend package;
native HTML5 DnD — no keyboard/touch support at all, which was the reason to reach for a library
in the first place.)

### 3.2 Interaction

- Every `ConversationItem` row becomes a **drag source** (`useDraggable`).
- Every section (`GroupSection`, `UngroupedSection`) becomes a **drop target** (`useDroppable`)
  over its whole body, not just the header — dropping anywhere in a section's chat list moves the
  chat into that group.
- **No reordering within a section** — chats stay sorted by `updated_at` (unchanged); a drop only
  changes `group_id`, never a manual order. Dropping onto the section a chat is already in is a
  no-op.
- Drop calls the same `useUpdateConversation({ id, groupId })` mutation the "Move to" menu already
  uses — no new backend endpoint.
- Visual feedback: dragged row gets reduced opacity + a drag overlay (dnd-kit's `DragOverlay`)
  following the cursor; the hovered drop target gets a highlighted border/background.
- **The "Move to" menu item stays** — it's not replaced, because dnd-kit's keyboard mode requires
  first tabbing to the row and pressing Space to "pick up" a keyboard drag, which is a real but
  less discoverable path; the explicit menu item remains the obvious one for both mouse users who
  don't want to drag and screen-reader users.

### 3.3 Non-goals

Dragging a *group section* to reorder groups themselves (groups aren't currently orderable at
all — they render in whatever order `GET /groups` returns). Multi-select drag (dragging several
chats at once).

## 4. Chat-native upload

### 4.1 Attach affordance

A paperclip icon button in `ChatInput`, to the left of the textarea, always visible (not tucked
under Filters — this is a primary action, same prominence as Send). Clicking it opens the native
file picker (same accept-list as `UploadDropzone`: PDF/DOCX/TXT/MD, same size cap).

### 4.2 Upload + attach flow

1. User picks a file → it uploads immediately (`useUploadDocument`), *before* the user finishes
   typing or sends anything — this mirrors ChatGPT (attach happens independently of send) and
   means upload errors (413/409/400) surface right away, not buried inside a chat turn.
2. While uploading, a **chip** appears above the textarea showing the filename + a spinner；once
   `stage()` returns, the chip switches to reflect processing status (same "pending" → "ready" /
   "failed" polling `DocumentList` already does, reused here via `useDocuments` or a lighter
   single-document status hook — implementation detail for the ticket).
3. **Group assignment on attach:**
   - If the current chat **already has a group** (existing conversation, or a brand-new chat
     started from within a group section), the file is uploaded straight into that group — no
     prompt.
   - If the current chat is **ungrouped**, uploading a file prompts once (a small inline popover
     anchored to the chip, reusing `GroupSelect`) — "Add to a group?" with the same
     None/existing-groups/+New-group options as the Documents page. Choosing "None" is valid and
     leaves it ungrouped like today's default.
4. The chip is dismissible (a small "x") — removing it does **not** delete the document (it's
   already a real row in Documents), it just clears it from the composer; this matches "attach is
   a shortcut to upload," not "attach is a temporary staging area."
5. Sending the message proceeds normally the moment the user hits Send — **it does not block on
   the document reaching `status: "ready"`.** If the user asks about the file before processing
   finishes, `retrieve_notes` simply won't find chunks for it yet (identical to today's behavior
   uploading via the Documents page then immediately asking in an existing chat) — no new
   backend behavior needed, and blocking Send would be a worse experience than the rare
   "processing didn't finish in time" case it would prevent.

### 4.3 Non-goals

Batch/multi-file attach in one action (attach one at a time, same as today's single-file
`UploadDropzone`). Editing a document's title/tags from the chip (that stays on Documents/the row's
`GroupSelect`+future tag editor). Removing/deleting the underlying document from the chip's "x."

## 5. Documents page redesign

Once most uploads happen from chat, `DocumentsPage`'s current "big upload form up top, list
below" layout inverts:

- **List becomes primary**: moves to the top, gets a **group filter** (a `GroupSelect`-style
  dropdown, or the same section-per-group grouping the Sidebar uses — TBD at ticket time) since
  `DocumentList` currently has no filtering UI despite the API already supporting `group_id`.
- **Upload becomes secondary**: collapses into a header action ("+ Upload") that opens the
  existing `UploadDropzone` + `MetadataFields` form in a dialog, rather than sitting permanently
  expanded on the page. The form's fields/behavior don't change, only where it lives.
- Everything downstream (`useDocuments`, `useUploadDocument`, `GroupSelect`, `DocumentRow`) is
  reused as-is — this is a layout/composition change, not a new data flow.

### 5.1 Non-goals

Search/sort on the document list (raised as a possibility, not asked for — separate ticket if
wanted later). Bulk actions (multi-select delete/move).

## 6. Delivery plan — tickets

Independent of each other except where noted; drag-and-drop can ship in parallel with the other
two.

- **T9 — Sidebar drag-and-drop**: add `@dnd-kit/core` + `@dnd-kit/sortable`; `ConversationItem`
  draggable, `GroupSection`/`UngroupedSection` droppable; drop calls the existing
  `useUpdateConversation`. Keyboard-drag verified; "Move to" menu unchanged.
- **T10 — Chat-native upload** *(no hard dependency, but land after T9 is fine either order)*:
  paperclip in `ChatInput`, upload-on-attach, status chip, group-assignment prompt for ungrouped
  chats, dismissible chip. Reuses `useUploadDocument`/`GroupSelect`.
- **T11 — Documents page redesign** *(needs T10 landed — the redesign's premise is that chat-
  upload exists)*: list-first layout, group filter on `DocumentList`, upload form moves into a
  dialog behind a header action.

## 7. Open questions for review

- **T9**: should a group section itself show a "drop here" empty-state hint even when it has
  chats, or only when empty? (Cosmetic, low-stakes — default to always-hoverable, decide visuals
  at implementation time.)
- **T10**: exact wording/UX of the one-time group-assignment prompt for an ungrouped chat's first
  attach — inline popover vs. a small modal. Leaning popover (lighter weight); confirm before
  building.
- **T11**: group filter as a dropdown (like Sidebar's old flat filter) vs. the same collapsible-
  sections layout the Sidebar uses for chats — the latter is more consistent but is more work.
  Flagged as TBD in §5, needs a decision before T11 starts.
