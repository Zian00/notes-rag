# Chat UX Backlog

**Status:** Not yet implemented — diagnosed, queued for a future session.

Three items surfaced during manual QA of the live app, in priority order.

## 1. Markdown rendering for assistant chat messages

`MessageBubble.tsx` renders `message.content` as plain `whitespace-pre-wrap` text — a deliberate v1 choice, not a bug (see the comment at that line). AI responses currently show as one unformatted paragraph.

**Agreed fix:** add `react-markdown` + `remark-gfm` (tables, strikethrough, task lists). Apply only to **assistant** messages — the user's own messages stay plain text, there's no need to markdown-render what someone typed. Must keep working with streaming partial content; `react-markdown` handles incomplete markdown gracefully (same behavior as ChatGPT/Claude's own UI — unclosed syntax just renders as plain text until it completes).

## 2. Chat response appears all-at-once instead of streaming token-by-token

Diagnosed, not yet fixed. Both ends of the code are already correctly built for real token streaming — this isn't a missing feature:

- Backend: `ChatService.stream_answer` (`backend/app/services/chat.py:137`) uses `self._graph.astream(inputs, config, stream_mode="messages")` — real incremental tokens as the LLM generates them.
- Frontend: `useChat.ts`'s `send()` (~line 182) appends each SSE `token` event's `delta` to the message as it arrives.

Most likely cause: buffering somewhere **between** them, not missing logic. Prime suspect is Vite's dev proxy (`frontend/vite.config.ts`, `/api → http://localhost:8000`) holding the SSE stream instead of flushing each chunk immediately. Needs a live test (browser Network tab, watch the response arrive over time) to confirm before touching any code — don't assume the proxy is the cause without checking.

## 3. Smarter query rewrite

Next item in the retrieval-quality roadmap (after reranking and hybrid search, both shipped — see `2026-07-26-ingestion-quality-design.md` §12 for the original 3-item sequence this continues). Currently the `rewrite` node (`backend/app/rag/graph/nodes.py:132`) blindly asks the LLM to rephrase the question after a failed grade — no visibility into *why* grading failed, what was actually retrieved, or chat history for resolving follow-ups like "what about that?".

Not yet grilled/speced — needs its own design discussion before implementation, same as reranking and hybrid search did.
