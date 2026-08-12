# notes-rag

Personal RAG study app — group notes/documents into study areas, chat against them, and control
exactly which documents each chat can retrieve from.

## Language

**Group**:
A named bucket that scopes both conversations and documents. Retrieval for a chat is strictly
limited to its own group's documents — grouping exists specifically to wall off retrieval, not
just to organize the sidebar.
_Avoid_: Course, folder, category

**Ungrouped**:
The chat/document state of having no group (`group_id = null`). Ungrouped is its own isolated
scope, not "no filter" — an ungrouped chat retrieves only ungrouped documents, mirroring every
other group's isolation.
_Avoid_: Default group, no group

**Attach** (chat-native upload):
The chat composer's shortcut action for uploading a document without leaving the chat. It creates
an ordinary `Document` row tagged with a group — there is no separate attachment entity. The
document IDs are persisted on the user-turn message (`attached_document_ids`) so the chat history
can render what was uploaded on each turn, but this link is purely for display: retrieval still
uses the group scope, not the per-message attachment list.
_Avoid_: Attachment (as a stored entity — it isn't one)
