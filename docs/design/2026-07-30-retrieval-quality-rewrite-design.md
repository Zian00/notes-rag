# Smarter Query Rewrite — Design

**Date:** 2026-07-30
**Status:** Implemented — shipped 2026-08-10 (commit `6f3ad50`; code-review follow-ups in `637f2f3`). See §10.
**Scope:** `backend/app/rag/graph/state.py`, `backend/app/rag/graph/nodes.py`, `backend/app/rag/graph/prompts.py`, `backend/app/rag/graph/builder.py`, associated tests
**Continues:** Retrieval-quality roadmap item 2 (reranking and hybrid search shipped already — see `docs/superpowers/specs/2026-07-26-ingestion-quality-design.md` §12 for the original 3-item sequence)

## 1. Motivation

Item 3 of the chat-UX backlog (`docs/superpowers/plans/2026-07-30-chat-ux-backlog.md`) flagged the `rewrite` node as doing a blind LLM rephrase with no visibility into:

1. *why* the grading step rejected the retrieved context,
2. *what was actually retrieved* instead, or
3. *chat history*, so follow-ups like "what about that?" never get resolved into something searchable.

Grilling this surfaced that (3) is really a distinct problem from (1)/(2): the existing `rewrite` node only ever runs *after* a failed grade, but a follow-up question needs resolving *before the first retrieval attempt* — otherwise it either wastes a retry or, worse, silently retrieves something plausible-but-wrong without ever triggering the corrective loop at all. This design addresses both.

It also surfaced a real, pre-existing bug: `rewrite` persists its rewritten query into the conversation's message history (`nodes.py:145`, `{"messages": [HumanMessage(new_q)]}`), and `ChatService.get_detail` (`chat.py:194-201`) echoes every `HumanMessage` back to the client as `"role": "user"`. So today, whenever a rewrite fires, the user's conversation history shows a synthetic query they never typed. This design fixes that as part of the same change, since a new node (`condense`) would otherwise just copy the same bug forward.

## 2. Current state (verified against code)

- `RagState` (`state.py`) has `messages`, `question`, `context`, `relevant`, `retry_count`. No field carries *why* grading failed.
- `grade` (`nodes.py:114-130`) returns only `Grade.relevant: bool` — no rationale.
- `rewrite` (`nodes.py:132-146`) sees only `state["question"]` (the literal original text) — never the failed `context`, never `state["messages"]`, and never a reason. It also injects `HumanMessage(new_q)` into persisted `messages` (the bug above).
- `agent` (`nodes.py:66-75`) receives full recent history (`_recent(state)`) when deciding tool-call args — so in principle it *could* write a history-aware `retrieve_notes` query itself, but nothing instructs it to, and `state["question"]` (used later by `grade`/citations) is never updated to match whatever query the agent actually used.
- The **linear** path (`AGENTIC_RETRIEVAL=false`, `builder.py:83-90`) is worse: `force_retrieve` passes `state["question"]` **verbatim** into `retrieve_notes`, with zero history awareness — the agent's tool-call reasoning isn't in the loop at all here.
- `generate` (`nodes.py:148-164`) already demonstrates the correct pattern for "give the LLM extra context for one call without persisting it": it builds an ad hoc `HumanMessage(f"Context:\n{ctx}")` for its own `ainvoke`, but never returns it in the state patch.

## 3. Goals

- A follow-up question is resolved into a standalone, searchable question **before** the first retrieval attempt, in both the agentic and linear paths.
- When a retrieval's context is graded irrelevant, the retry query is informed by *why* it failed and *what was actually retrieved*, not a blind rephrase of the original question.
- Neither of the above ever writes a synthetic message into the persisted conversation transcript — `GET /conversations/{id}` always reflects exactly what the user typed and what the assistant answered.

## 4. Non-goals (explicitly deferred)

- HyDE, multi-query fusion, or any other rewrite *strategy* beyond what's described here.
- Changing `max_grade_retries` (stays 2) or any other existing config default.
- Any change to reranking or hybrid search (already shipped, orthogonal to this).
- The known `get_document_content` citation title/filename-mislabeling bug — that belongs to the next roadmap item (Generation correctness, §12 item 3), not this one.
- Any UI surfacing of the resolved/rewritten query — this is internal graph plumbing only.
- Changes to `list_documents` / `get_document_content` — they take structured args (`course`, `document_id`), not a free-text query, so condensing doesn't apply to them.

## 5. Design — Part A: `grade` explains itself, `rewrite` uses the explanation

### 5.1 `Grade` schema gains a reason

```python
class Grade(BaseModel):
    relevant: bool = Field(description="True if the context can answer the question.")
    reason: str = Field(
        description="One sentence: why the context is or isn't sufficient — "
        "what's missing or mismatched, if not relevant."
    )
```

No extra LLM call — `grader` already returns a structured object via `with_structured_output`; this just widens the schema. `grade` stores it: `return {"relevant": verdict.relevant, "grade_reason": verdict.reason}`.

### 5.2 `RagState` gains `grade_reason`

```python
grade_reason: str  # grader's explanation for the last verdict; consumed by rewrite
```

### 5.3 `rewrite` uses the reason *and* the failed context

```python
async def rewrite(state: RagState, config: RunnableConfig) -> dict[str, Any]:
    """Rewrite the question for better retrieval, informed by why the last attempt failed.

    Unlike a blind rephrase, this sees grade's reason and the actual chunks that were
    retrieved-but-rejected, so it can pick up vocabulary mismatches (e.g. notes say
    "back-propagation", the question said "backprop") instead of guessing blind.
    """
    ctx = format_chunks_for_llm(state.get("context", []))
    reason = state.get("grade_reason", "")
    resp = await model.ainvoke(
        [
            SystemMessage(REWRITE_SYSTEM),
            HumanMessage(
                f"Original question: {state['question']}\n\n"
                f"Why the last search failed: {reason}\n\n"
                f"What was retrieved instead:\n{ctx}"
            ),
        ],
        config,
    )
    new_q = resp.content if isinstance(resp.content, str) else str(resp.content)
    return {"question": new_q, "retry_count": state.get("retry_count", 0) + 1}
    # No "messages" patch — see §6.3 for how the agent picks up the new question
    # without it being persisted into the visible conversation.
```

`REWRITE_SYSTEM` updates to reference the reason/context it's now given, instead of asking for a blind rephrase.

## 6. Design — Part B: resolving follow-ups with a `condense` node

### 6.1 New node, placed before both paths' entry point

A new `condense` node sits between `START` and the existing entry point (`agent` for the agentic path, `force_retrieve` for the linear path):

```python
g.add_edge(START, "condense")
g.add_edge("condense", "agent" if settings.agentic_retrieval else "force_retrieve")
```

`rewrite`'s loop-back edges are **unchanged** (`rewrite → agent` / `rewrite → force_retrieve` directly) — condense is a turn-start operation, not something that should re-run on every corrective retry within a turn.

### 6.2 Node body — skipped on a conversation's first message

```python
async def condense(state: RagState, config: RunnableConfig) -> dict[str, Any]:
    """Resolve follow-up references in the latest question using prior conversation turns.

    Skipped on a conversation's first message: there is no prior turn to resolve
    references against, so the LLM call would be a pure latency cost with no effect.
    Only `question` changes here — see §6.3 for how the agent surfaces the resolved
    question to its own tool-call reasoning without polluting the persisted transcript.
    """
    if len(state["messages"]) <= 1:
        return {}
    resp = await model.ainvoke([SystemMessage(CONDENSE_SYSTEM), *_recent(state)], config)
    new_q = resp.content if isinstance(resp.content, str) else str(resp.content)
    return {"question": new_q}
```

`CONDENSE_SYSTEM` instructs the model to rewrite the latest message into a standalone question using the conversation so far, resolving pronouns/implicit references, and to return the message **unchanged** if it's already standalone or needs no notes lookup (a greeting, a meta question) — bounding the risk of the condense step mangling a question that didn't need touching.

This node runs regardless of `AGENTIC_RETRIEVAL`, so the linear path — which never gives the agent a chance to reason over history at all — gets the same fix. `force_retrieve` already reads `state["question"]` directly for its `retrieve_notes` args, so it picks up the condensed question with no further change needed.

### 6.3 `agent` surfaces the current question without touching persisted messages

Both `condense` and `rewrite` update `state["question"]`, but the agentic path's actual `retrieve_notes` call is decided by the `agent` node's own LLM call reading `_recent(state)` — which doesn't reflect `state["question"]` at all today. Rather than inject a fake `HumanMessage` into `messages` (the bug this design fixes), `agent` appends an **ephemeral, non-persisted** note for its own call only — the same pattern `generate` already uses for its context block:

```python
async def agent(state: RagState, config: RunnableConfig) -> dict[str, Any]:
    msgs = [SystemMessage(AGENT_SYSTEM), *_recent(state)]
    if state.get("question"):
        msgs.append(HumanMessage(f"(Current question to answer: {state['question']})"))
    resp = await agent_model.ainvoke(msgs, config)
    return {"messages": [resp]}  # only the model's own response is persisted
```

This single change covers both cases: on a turn's first pass, `state["question"]` reflects whatever `condense` produced; on a retry after `rewrite`, it reflects the rewritten query. Neither is ever written back into the conversation's persisted `messages`, so `rewrite`'s `{"messages": [HumanMessage(new_q)]}` line is simply removed (§5.3) rather than replaced with something equivalent — the mechanism moves to `agent`, once, rather than being duplicated in every node that changes `question`.

## 7. Testing strategy

Following the existing pattern (fakes + `MemorySaver`, per `docs/superpowers/specs/2026-06-16-agentic-rag-design.md` §8):

- `condense`: skipped when `len(messages) <= 1` (no LLM call made — assert on the fake model's call count); resolves a fake follow-up + history into a standalone question; leaves an already-standalone question's routing unaffected (unit-testable via fixed fake responses).
- `grade`: `reason` flows through into the returned state patch alongside `relevant`.
- `rewrite`: prompt includes both `grade_reason` and the failed context (assert on what's passed to the fake model); **no longer returns a `messages` patch** — regression test asserting the state patch has no `messages` key.
- `agent`: appends the ephemeral question note when `state["question"]` is set; the returned state patch contains only the model's response, never the ephemeral note (regression test for §6.3).
- Integration: a two-turn conversation with a follow-up ("what about that?") resolves and retrieves correctly with a fake LLM; `GET /conversations/{id}` history contains exactly the user's literal messages and the assistant's replies — no synthetic entries, in both the agentic and linear (`AGENTIC_RETRIEVAL=false`) paths.

## 8. Risks / notes

- **Extra LLM call cost:** `condense` adds one call per turn after the first (skipped on turn 1). Accepted per-turn latency/cost tradeoff for fixing both retrieval paths uniformly — see the grilled discussion on node placement.
- **Condense could over-resolve** a question that didn't need it, subtly changing its meaning. Bounded by explicitly instructing the model to return the message unchanged when it's already standalone; not a hard guarantee, same class of risk as any prompt-driven rewrite.
- **Behavior change, not a migration:** existing conversations that already have a synthetic rewritten message persisted from before this fix keep that message as-is (no backfill); only turns going forward stop adding new ones.
- **Grounding is still prompt-enforced, not guaranteed** — same caveat that applies to `rewrite`/`generate` today.

## 9. Roadmap context

This closes out retrieval-quality roadmap item 2 (reranking, hybrid search, smarter rewrite — all three now addressed). Item 3, **generation correctness** (hallucination checking, citation fidelity, including the known `get_document_content` title/filename mislabeling bug), is next and not yet grilled.

## 10. Implementation notes (2026-08-10)

Shipped as designed, with three details that differ from the draft's placeholders:

- **Shared helper location:** the document-dedup helper landed at `backend/app/services/citations.py` (`dedupe_chunks_by_document` + `to_citations`), not the tentatively-named `app/rag/citations.py` (§5.1/§6). `format_chunks_for_llm` imports it from there.
- **`grade` return shape:** `grade` returns `context_rejected` (not a bare `relevant`) alongside `grade_reason` (`nodes.py`); the reason flows into `rewrite` exactly as §5.3 describes.
- **Linear-path entry:** after `condense`, the linear path enters a `triage` node (`builder.py`) which then routes toward `force_retrieve` when notes are needed — §6.1 sketched `condense → force_retrieve` directly, collapsing a routing step that now exists explicitly.

Covered by `backend/tests/test_graph_nodes.py`, `test_graph_flow.py`, and `test_graph_tools.py`.
