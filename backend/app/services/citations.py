"""Shared citation-numbering rule + the client-facing citation projection.

Both ``format_chunks_for_llm`` (the LLM-facing context renderer, in
``app.rag.graph.tools``) and ``to_citations`` (the client-facing citations
array) need to agree on which chunks represent the "same source" so that an
LLM-written ``[n]`` marker always corresponds to ``citations[n-1]``.
This is the single place that dedup rule lives, so the two can never
independently drift out of sync.
"""

from typing import Any

# The subset of chunk keys that is safe (and useful) to hand to the client.
_CITATION_FIELDS = (
    "chunk_id",
    "document_id",
    "filename",
    "title",
    "page_number",
    "section",
    "score",
)


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


def to_citations(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim context chunks to the citation-safe subset of keys for the client.

    Deduped by document via ``dedupe_chunks_by_document``, so this array's
    ordering always agrees with the citation numbers ``format_chunks_for_llm``
    showed the LLM — an inline "[n]" marker in the answer always corresponds to
    ``citations[n-1]``.
    """
    return [
        {k: c.get(k) for k in _CITATION_FIELDS} for c in dedupe_chunks_by_document(context)
    ]
