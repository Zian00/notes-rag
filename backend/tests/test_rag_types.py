from app.rag.types import Chunk, ParsedDocument, Segment


def test_parsed_document_holds_segments():
    seg = Segment(text="hello world", page_number=1, section="Intro")
    doc = ParsedDocument(segments=[seg], page_count=1)
    assert doc.segments[0].text == "hello world"
    assert doc.page_count == 1


def test_chunk_carries_segment_metadata():
    c = Chunk(content="hi", chunk_index=0, page_number=2, section="Topic", token_count=1)
    assert c.page_number == 2 and c.section == "Topic"
