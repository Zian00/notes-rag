from app.rag.parsing.heading_split import split_markdown_by_headings
from app.rag.types import ParsedDocument, Segment


class TextParser:
    """TXT -> one segment. Markdown -> one segment per heading block, with a
    breadcrumb `section` for nested headings (see heading_split.py)."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        text = data.decode("utf-8", errors="replace")
        if content_type != "text/markdown":
            return ParsedDocument(segments=[Segment(text=text)], page_count=None)
        return ParsedDocument(segments=split_markdown_by_headings(text), page_count=None)
