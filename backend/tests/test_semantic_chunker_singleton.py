"""SemanticChunker() loads a fastembed ONNX model — expensive to construct fresh
on every call. get_chunker() (app.api.deps) is a dependency of get_ingestion_service,
which BOTH POST /documents and POST /documents/{id}/replace depend on, even though
neither endpoint's stage()/stage_replace() ever touches the chunker. This asserts
the chunker-construction path is memoized (constructed once, same instance
returned thereafter) rather than rebuilt per call, both for the API dependency and
the background job's equivalent construction.
"""

from app.api.deps import get_semantic_chunker
from app.jobs.ingestion_tasks import _get_semantic_chunker


def test_get_semantic_chunker_returns_same_instance_across_calls():
    first = get_semantic_chunker()
    second = get_semantic_chunker()
    assert first is second


def test_job_semantic_chunker_returns_same_instance_across_calls():
    first = _get_semantic_chunker()
    second = _get_semantic_chunker()
    assert first is second
