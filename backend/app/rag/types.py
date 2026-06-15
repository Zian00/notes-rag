from dataclasses import dataclass, field


@dataclass(frozen=True)
class Segment:
    """A structural unit of a parsed document (a page, slide, or heading block)."""

    text: str
    page_number: int | None = None
    section: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    """Output of a DocumentParser: ordered segments + page/slide count."""

    segments: list[Segment] = field(default_factory=list)
    page_count: int | None = None


@dataclass(frozen=True)
class Chunk:
    """A chunk ready to embed and persist."""

    content: str
    chunk_index: int
    page_number: int | None = None
    section: str | None = None
    token_count: int | None = None
