import io

from docx import Document as DocxDocument
from pptx import Presentation

from app.rag.types import ParsedDocument, Segment


class PptxParser:
    """One segment per slide; section = slide title, page_number = slide number."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        prs = Presentation(io.BytesIO(data))
        segments: list[Segment] = []
        # One slide → one segment. enumerate(..., start=1) gives a 1-based slide number we
        # store as page_number (so retrieval can cite "slide 3").
        for index, slide in enumerate(prs.slides, start=1):
            # Read the title via slide.shapes.title.text directly. (Comparing shapes with
            # `shape == slide.shapes.title` does NOT work — python-pptx hands back fresh
            # proxy objects each access, so the identity check is always False.)
            title_shape = slide.shapes.title
            title = (
                title_shape.text
                if title_shape is not None and title_shape.text
                else None
            )
            # Collect text from every shape that has a text frame (title + body + textboxes).
            texts = [
                shape.text_frame.text
                for shape in slide.shapes
                if shape.has_text_frame and shape.text_frame.text
            ]
            segments.append(
                Segment(text="\n".join(texts), page_number=index, section=title)
            )
        return ParsedDocument(segments=segments, page_count=len(segments))


class DocxParser:
    """Segments split on heading-styled paragraphs; else a single segment."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        doc = DocxDocument(io.BytesIO(data))
        segments: list[Segment] = []
        current_heading: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            body = "\n".join(buffer).strip()
            if body or current_heading:
                segments.append(Segment(text=body, section=current_heading))

        # Same flush-on-heading pattern as Markdown, but here "heading" is a Word paragraph
        # STYLE (e.g. "Heading 1"), not a '#'. Word has no fixed page count, so page_count=None.
        for para in doc.paragraphs:
            style = (para.style.name or "").lower() if para.style else ""
            if style.startswith("heading") and para.text.strip():
                flush()
                buffer = []
                current_heading = para.text.strip()
            elif para.text.strip():
                buffer.append(para.text)
        flush()
        if not segments:
            segments = [Segment(text="")]  # guarantee at least one segment for an empty doc
        return ParsedDocument(segments=segments, page_count=None)
