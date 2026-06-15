from app.rag.types import ParsedDocument, Segment


class TextParser:
    """TXT → one segment. Markdown → one segment per heading block (section = heading)."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        text = data.decode("utf-8", errors="replace")
        if content_type != "text/markdown":
            return ParsedDocument(segments=[Segment(text=text)], page_count=None)
        return ParsedDocument(segments=_split_markdown(text), page_count=None)


def _split_markdown(text: str) -> list[Segment]:
    # Walk the lines, accumulating body text into `buffer`. Every time we hit a heading
    # ("# ...") we "flush" the accumulated body as one segment tagged with the heading we
    # were under, then start a new section. This makes each heading block its own segment.
    segments: list[Segment] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        # Emit the buffered body as a segment (skip truly-empty leading content).
        body = "\n".join(buffer).strip()
        if body or current_heading:
            segments.append(Segment(text=body, section=current_heading))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):  # a Markdown heading line starts a new section
            flush()
            buffer = []
            current_heading = stripped.lstrip("#").strip()  # drop the leading '#'s
        else:
            buffer.append(line)
    flush()  # don't forget the final section after the loop ends
    return segments or [Segment(text=text)]  # fallback: a doc with no headings = one segment
