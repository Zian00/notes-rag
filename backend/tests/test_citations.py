"""Tests for the shared citation-numbering helper.

format_chunks_for_llm (LLM-facing) and to_citations (client-facing)
must agree on which chunks represent the "same source" so an LLM-written "[n]"
marker always corresponds to citations[n-1] — these tests prove the shared rule
they both build on, and that the two functions actually agree given the same input.
"""

from typing import Any

from app.rag.graph.tools import format_chunks_for_llm
from app.services.citations import dedupe_chunks_by_document, to_citations


def _chunk(document_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "chunk_id": f"chunk-{document_id}-{overrides.get('page_number', 0)}",
        "document_id": document_id,
        "filename": f"{document_id}.pdf",
        "title": f"Title {document_id}",
        "content": f"content for {document_id}",
        "page_number": None,
        "section": None,
        "score": 0.9,
    }
    base.update(overrides)
    return base


def test_dedupe_chunks_by_document_first_occurrence_wins() -> None:
    chunks = [_chunk("a"), _chunk("b"), _chunk("a"), _chunk("c"), _chunk("b")]
    deduped = dedupe_chunks_by_document(chunks)
    assert [c["document_id"] for c in deduped] == ["a", "b", "c"]
    # The FIRST occurrence of each document is kept, not the last.
    assert deduped[0] is chunks[0]
    assert deduped[1] is chunks[1]


def test_dedupe_chunks_by_document_empty() -> None:
    assert dedupe_chunks_by_document([]) == []


def test_llm_numbering_and_client_citations_agree() -> None:
    """The core guarantee this whole design rests on: whatever number
    format_chunks_for_llm printed next to a chunk is exactly the 1-based position
    of that chunk's document in to_citations' output."""
    chunks = [_chunk("doc-a"), _chunk("doc-b"), _chunk("doc-a"), _chunk("doc-c")]

    rendered = format_chunks_for_llm(chunks)
    citations = to_citations(chunks)

    assert [c["document_id"] for c in citations] == ["doc-a", "doc-b", "doc-c"]
    assert "[1] Title doc-a" in rendered
    assert "[2] Title doc-b" in rendered
    assert "[3] Title doc-c" in rendered
    # doc-a's second chunk must reuse [1] — matching citations[0], not citations[2].
    assert rendered.count("[1] Title doc-a") == 2
