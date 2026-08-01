"""Shared citation-numbering rule.

Both ``format_chunks_for_llm`` (the LLM-facing context renderer, in
``app.rag.graph.tools``) and ``ChatService._to_citations`` (the client-facing
citations array) need to agree on which chunks represent the "same source" so
that an LLM-written ``[n]`` marker always corresponds to ``citations[n-1]``.
This is the single place that dedup rule lives, so the two can never
independently drift out of sync.
"""

from typing import Any


def dedupe_chunks_by_document(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return chunks deduped by document_id, first occurrence wins.

    Whole-document tool calls produce one chunk per page; without this dedup a
    single document would appear once per chunk instead of once per source.
    """
    seen: set[Any] = set()
    result: list[dict[str, Any]] = []
    for c in chunks:
        doc_id = c.get("document_id")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        result.append(c)
    return result
