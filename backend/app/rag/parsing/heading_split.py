from app.rag.types import Segment


def split_markdown_by_headings(text: str, page_number: int | None = None) -> list[Segment]:
    """Splits Markdown text on '#' heading lines into one Segment per heading
    block. `section` is a '>'-joined breadcrumb of the current heading stack
    (e.g. 'Lecture 4 > Neural Networks'), not just the innermost heading, so
    nested structure survives into citations. Shared by TextParser's .md path
    and the PDF parser (over pymupdf4llm's Markdown output)."""
    segments: list[Segment] = []
    heading_stack: list[tuple[int, str]] = []  # (level, text), outermost first
    buffer: list[str] = []

    def breadcrumb() -> str | None:
        return " > ".join(h[1] for h in heading_stack) or None

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body or heading_stack:
            segments.append(Segment(text=body, section=breadcrumb(), page_number=page_number))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            flush()
            buffer = []
            level = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped.lstrip("#").strip()
            # Pop headings at this level or deeper — keeps the stack representing
            # the current nesting path (e.g. a new "##" replaces the previous "##"
            # but keeps any enclosing "#").
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_text))
        else:
            buffer.append(line)
    flush()
    return segments or [Segment(text=text, page_number=page_number)]
