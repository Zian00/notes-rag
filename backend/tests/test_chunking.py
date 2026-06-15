from app.rag.chunking import Chunker
from app.rag.types import ParsedDocument, Segment


def _chunker(tokens: int = 30, overlap: int = 5) -> Chunker:
    return Chunker(chunk_tokens=tokens, chunk_overlap_tokens=overlap)


def test_small_segment_stays_one_chunk_and_keeps_metadata():
    doc = ParsedDocument(segments=[Segment("short text", page_number=2, section="Intro")])
    chunks = _chunker().split(doc)
    assert len(chunks) == 1
    assert chunks[0].page_number == 2
    assert chunks[0].section == "Intro"
    assert chunks[0].chunk_index == 0
    assert (chunks[0].token_count or 0) > 0


def test_never_merges_across_segments():
    doc = ParsedDocument(
        segments=[
            Segment("alpha beta", page_number=1, section="A"),
            Segment("gamma delta", page_number=2, section="B"),
        ]
    )
    chunks = _chunker().split(doc)
    by_page = {c.page_number for c in chunks}
    assert by_page == {1, 2}
    for c in chunks:
        if c.page_number == 1:
            assert "gamma" not in c.content
        if c.page_number == 2:
            assert "alpha" not in c.content


def test_large_segment_is_split_with_increasing_indexes():
    big = " ".join(f"word{i}" for i in range(300))
    doc = ParsedDocument(segments=[Segment(big, page_number=1, section="Big")])
    chunks = _chunker(tokens=30, overlap=5).split(doc)
    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.section == "Big" for c in chunks)


def test_blank_segments_are_skipped():
    doc = ParsedDocument(segments=[Segment("   ", page_number=1), Segment("real", page_number=2)])
    chunks = _chunker().split(doc)
    assert len(chunks) == 1
    assert chunks[0].page_number == 2
