# Markdown Rendering + Citation Fidelity — Design

**Date:** 2026-07-31
**Status:** Draft, awaiting review
**Scope:** `frontend/src/components/chat/MessageBubble.tsx`, `frontend/src/components/chat/Citations.tsx`, two new custom remark/rehype plugins, `frontend/package.json`; `backend/app/rag/graph/tools.py`, `backend/app/services/chat.py`, a new shared citation-numbering helper
**Continues:** Chat UX backlog item 1 (`docs/superpowers/plans/2026-07-30-chat-ux-backlog.md`) — and, since grilling it surfaced a blocking dependency, partially closes the retrieval-quality roadmap's "generation correctness" item (the **citation-fidelity** slice only)

---

## 1. Motivation

`MessageBubble` renders assistant answers as plain `whitespace-pre-wrap` text — a deliberate v1 choice, but it means no bold, lists, tables, or code formatting ever shows up, even though the model is instructed to write structured answers.

Grilling the natural follow-on idea — making inline `[n]` citation markers clickable, tying them to the `Citations` component — surfaced a real correctness bug: the marker numbers the LLM writes and the indices of the `citations` array sent to the client are computed independently and can silently disagree. Making markers clickable on top of that would ship something actively worse than today (pointing at the wrong source), so fixing the numbering became part of this pass rather than a separate one.

## 2. Current state (verified against code)

- `MessageBubble.tsx:27` renders plain text via `whitespace-pre-wrap` (comment at lines 25-26 confirms this is intentional, not an oversight).
- No markdown or typography library is installed (`frontend/package.json` checked directly).
- **Correction (see §7):** tests live in a top-level `frontend/tests/` directory, not colocated under `frontend/src/` — an initial `Glob("frontend/src/**/*.test.*")` missed this and wrongly suggested no tests existed. `frontend/tests/chat-ui.test.tsx` (369 lines) already covers the chat flow end-to-end via a full-app render + MSW + scripted SSE frames.
- `useChat.ts` appends SSE token deltas via string concatenation into `message.content` — a flat string, growing over time.
- `next-themes` is already used app-wide for dark/light theme switching.
- `Citations.tsx:10-12`'s own comment: citations are "deduped by document server-side, not a strict 1:1 mapping to the answer's inline `[n]` markers" — which is exactly why it renders an unordered "Sources" list today instead of trying to line markers up with entries.
- `format_chunks_for_llm` (`backend/app/rag/graph/tools.py:138-151`) numbers context chunks `1..k` in retrieval order — one number **per chunk**.
- `_to_citations` (`backend/app/services/chat.py:34-41`) separately dedupes that same context list by `document_id`, first-occurrence-wins, producing a shorter array indexed **per unique document**.
- `GENERATE_SYSTEM` (`backend/app/rag/graph/prompts.py`) instructs: *"Cite sources inline like [1], [2] matching the numbered context."* — the model's numbers come from `format_chunks_for_llm`'s chunk-level numbering, not from `_to_citations`'s document-level array.

## 3. Goals

- Assistant messages render real markdown (bold, italic, lists, tables, strikethrough, task lists, syntax-highlighted code blocks). User messages and error messages stay on today's plain-text path, unchanged.
- No assistant content can trigger an automatic network request when rendered (no auto-loading images).
- Links are safe to open (`target="_blank" rel="noopener noreferrer"`).
- The streaming "typing" cursor still renders inline at the true end of the rendered content, regardless of what markdown structure that end happens to be (paragraph, list, table, code block).
- `[n]` citation markers in the rendered text reliably correspond to `citations` array entries, **by construction** — not by a fragile after-the-fact remapping. Clicking one reveals/highlights the matching entry in the existing `Citations` component.
- This repo's first frontend test suite, covering the security-relevant behaviors (images never rendered, link safety, user/error messages never markdown-parsed) alongside formatting correctness and citation-marker behavior.

## 4. Non-goals (explicitly deferred)

- `@tailwindcss/typography` / `prose`-based styling — hand-styled `components` overrides instead, to stay consistent with the bubble's existing compact design tokens.
- Hallucination-checking and the known `get_document_content` title/filename mislabeling bug — both remain deferred parts of the "generation correctness" roadmap item. This pass only fixes citation **numbering** fidelity.
- Merging same-document chunks into one combined block for the LLM — chunks stay separate and distinct in what the model reasons over; only the *number* printed next to each one changes.
- Token-by-token streaming (chat-ux-backlog item 2) — separate, unstarted work.

## 5. Design — Backend: citation numbering fidelity

### 5.1 Shared document-dedup helper

A new shared helper (exact module TBD at implementation time, likely `app/rag/citations.py`) computes the "first `document_id` occurrence wins" ordering once, used by both:

- **`format_chunks_for_llm`** (`tools.py`) — numbers each chunk by its *document's* position in this ordering, not the chunk's position. Two chunks from the same document both render under the same number.
- **`_to_citations`** (`chat.py`) — already implements this exact dedup rule inline; refactored to call the shared helper instead, so the two can never drift apart even if one is edited later.

### 5.2 `format_chunks_for_llm` changes

Given a `document_id → number` mapping computed once via the shared helper, each chunk renders as `[{doc_number}] {title}...` instead of `[{chunk_index}] {title}...`. Chunk content is untouched — grading and generation still see every chunk individually — only the citation number changes to reflect "which document," matching `citations`' indexing exactly.

## 6. Design — Frontend: markdown rendering

### 6.1 Rendering pipeline

`react-markdown` + `remark-gfm` (tables, strikethrough, task lists), applied only when `!isUser && !message.error`. User messages and error messages keep today's `whitespace-pre-wrap` path unchanged — never parsed as markdown.

### 6.2 Custom component overrides (no typography plugin)

Hand-styled `components` prop mapping onto existing Tailwind tokens (`text-foreground`, `bg-muted`, etc.): `p`, `ul`/`ol`/`li`, `strong`/`em`, `blockquote`, `hr`, `table`/`thead`/`tbody`/`tr`/`th`/`td`, `a`, `code`/`pre`, `img`.

### 6.3 Images disabled

The `img` override renders the alt text only (never an actual `<img>` tag) — closing off the markdown-image auto-fetch/exfiltration vector a future indirect-prompt-injection in an uploaded document could otherwise trigger.

### 6.4 Links

The `a` override adds `target="_blank" rel="noopener noreferrer"` to every link. (Fact, not something this design adds: `react-markdown` already neutralizes dangerous URL schemes like `javascript:` by default.)

### 6.5 Code blocks — syntax highlighting

`react-syntax-highlighter`, driven by a custom `code`/`pre` component. Theme selected via `useTheme()` from `next-themes`, matching the app's existing dark/light switching rather than a static stylesheet.

### 6.6 Streaming cursor — custom AST transform

A small custom remark/rehype plugin (using `unist-util-visit-parents` or equivalent) that, when `isStreamingThisMessage` is true, walks the parsed tree to find the actual last text node — wherever it is — and splices the existing `animate-pulse`-styled cursor `<span>` in right after it. Preserves today's exact inline cursor behavior for ordinary text, lists, and tables. **Exception:** if the last text node would land inside a `code` element, the cursor is appended after the whole tree instead of spliced into the code block's own text (see §8) — code content is opaque to this plugin, since it renders via a separate syntax-highlighter path that takes the whole block as one raw string, not hast children.

### 6.7 Citation-marker linking

A second custom remark/rehype plugin scans text nodes for `\[(\d+)\]`-shaped substrings and converts matches into a distinct node type, mapped via `components` to a small `CitationMarker` component. Clicking one expands (if collapsed) and scrolls to/highlights the matching entry in the existing `Citations` component. A marker with no matching `citations` entry (out of range) falls back to plain unclickable text — a safety net for any LLM misnumbering that survives the §5 backend fix.

## 7. Testing strategy

**Correction from the original draft:** this is *not* the repo's first frontend test suite — `frontend/tests/` already has 12 files (~2000+ lines), including a 369-line `chat-ui.test.tsx` that exercises the chat flow end-to-end (full-app render via a `renderApp` helper, MSW-mocked network, `vi.mock("@/api/chatStream")` to script SSE frames, assertions via accessible roles/text through Testing Library). New tests extend that existing pattern rather than unit-testing `MessageBubble` in isolation with a hand-built prop object.

- **`chat-ui.test.tsx` additions:** script a `mockStreamChat` sequence whose assistant content contains markdown (bold/list/table/code); assert the rendered DOM has real elements (`<strong>`, `<li>`, `<table>`, syntax-highlighted `<pre>`), not literal `**`/`-`/`|` characters. A user-typed message containing markdown-like syntax renders literally (already implicitly covered by the existing "line one\nline two" `<p>` assertion pattern — extended to confirm markdown characters in a *user* message never parse).
- **Images:** a scripted assistant message containing `![alt](url)` never produces an `<img>` element anywhere in the rendered output.
- **Links:** a scripted assistant message containing `[text](url)` renders an anchor with `target="_blank" rel="noopener noreferrer"`.
- **Streaming cursor:** using the existing hanging-stream pattern (`chat-ui.test.tsx`'s "allows typing while streaming" test), assert the cursor element appears after the true last rendered text across a couple of structures (plain text, inside a list).
- **Citation markers:** extends the existing "shows citations on expand" test — a `[1]` marker in scripted content is clickable and reveals/highlights the matching `Citations` entry; a marker number with no matching citation renders as plain text.
- **Backend:** `format_chunks_for_llm` numbers by document, not by chunk (unit test with 2+ chunks sharing a `document_id`); a test asserting `format_chunks_for_llm` and `_to_citations` agree on numbering for the same input context, via the shared helper. (Implemented in `backend/tests/test_citations.py` and `test_graph_tools.py`.)

## 8. Risks / notes

- Two custom remark/rehype plugins (cursor placement, citation-marker detection) are genuinely new surface area — first use of the unified/remark plugin API in this codebase.
- Residual edge case: CommonMark could in principle parse `[1]` as the start of a link/reference construct in unusual cases (e.g. if an answer happens to also contain a matching reference-style link definition). Extremely unlikely given `GENERATE_SYSTEM`'s plain `"[1], [2]"` instruction — noted, not fully eliminated.
- `react-syntax-highlighter` bundle size / language-subset choice is deferred to implementation time (import only the languages actually needed vs. the full bundle).
- (Superseded — see the §7 correction: this extends the existing `frontend/tests/` suite, it doesn't establish a new convention.)
- The citation-numbering change is a backend behavior change, but needs no migration: citation numbers are computed fresh from `state["context"]` on every request via `_to_citations`, never persisted pre-numbered.

## 9. Roadmap context

Closes chat-ux-backlog item 1 (markdown rendering) and the citation-fidelity slice of the "generation correctness" roadmap item. Still open: chat-ux-backlog item 2 (token-by-token streaming), and the rest of "generation correctness" (hallucination-checking, the `get_document_content` title/filename mislabeling bug).
