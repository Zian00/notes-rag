import io

from docx import Document as DocxDocument
from pptx import Presentation

from app.rag.types import ParsedDocument, Segment


class PptxParser:
    """One segment per slide; section = slide title, page_number = slide number."""

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        prs = Presentation(io.BytesIO(data))
        segments: list[Segment] = []
        for index, slide in enumerate(prs.slides, start=1):
            title_shape = slide.shapes.title
            title = (
                title_shape.text
                if title_shape is not None and title_shape.text
                else None
            )
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
            segments = [Segment(text="")]
        return ParsedDocument(segments=segments, page_count=None)
