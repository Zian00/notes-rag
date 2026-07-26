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


def test_segment_that_fits_stays_as_one_chunk_with_no_forced_split():
    chunker = Chunker(chunk_tokens=100, chunk_overlap_tokens=10)
    doc = ParsedDocument(segments=[Segment(text="A short segment.", section="Intro")])
    chunks = chunker.split(doc)
    assert len(chunks) == 1
    assert chunks[0].content == "A short segment."


def test_oversized_segment_without_semantic_chunker_uses_fixed_size_split():
    chunker = Chunker(chunk_tokens=5, chunk_overlap_tokens=1)
    long_text = " ".join(["word"] * 50)
    doc = ParsedDocument(segments=[Segment(text=long_text)])
    chunks = chunker.split(doc)
    assert len(chunks) > 1


def test_oversized_segment_with_semantic_chunker_delegates_to_it():
    class FakeSemanticChunker:
        def split(self, text: str) -> list[str]:
            return ["first half.", "second half."]

    chunker = Chunker(
        chunk_tokens=3, chunk_overlap_tokens=1, semantic_chunker=FakeSemanticChunker()
    )
    long_text = " ".join(["word"] * 20)
    doc = ParsedDocument(segments=[Segment(text=long_text)])
    chunks = chunker.split(doc)
    assert [c.content for c in chunks] == ["first half.", "second half."]


def test_semantic_piece_still_too_large_falls_back_to_fixed_size_split():
    class FakeSemanticChunker:
        def split(self, text: str) -> list[str]:
            return [text]  # doesn't actually shrink it

    chunker = Chunker(
        chunk_tokens=3, chunk_overlap_tokens=1, semantic_chunker=FakeSemanticChunker()
    )
    long_text = " ".join(["word"] * 20)
    doc = ParsedDocument(segments=[Segment(text=long_text)])
    chunks = chunker.split(doc)
    # Falls through to the fixed-size splitter as the final safety net.
    assert len(chunks) > 1
