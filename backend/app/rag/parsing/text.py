from app.rag.types import ParsedDocument, Segment


class TextParser:
    """TXT → one segment. Markdown → one segment per heading block (section = heading)."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        text = data.decode("utf-8", errors="replace")
        if content_type != "text/markdown":
            return ParsedDocument(segments=[Segment(text=text)], page_count=None)
        return ParsedDocument(segments=_split_markdown(text), page_count=None)


def _split_markdown(text: str) -> list[Segment]:
    segments: list[Segment] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or current_heading:
            segments.append(Segment(text=body, section=current_heading))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            flush()
            buffer = []
            current_heading = stripped.lstrip("#").strip()
        else:
            buffer.append(line)
    flush()
    return segments or [Segment(text=text)]
