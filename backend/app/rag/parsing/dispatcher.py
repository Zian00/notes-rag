from app.rag.ocr import OcrProvider
from app.rag.parsing.image import ImageParser
from app.rag.parsing.office import DocxParser, PptxParser
from app.rag.parsing.pdf import PdfParser
from app.rag.parsing.text import TextParser
from app.rag.types import ParsedDocument

_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UnsupportedContentType(Exception):
    """Raised when no parser handles the given content type."""


class ParserDispatcher:
    """Selects the right parser adapter by (already-sniffed) content type."""

    def __init__(self, ocr: OcrProvider, ocr_enabled: bool, min_chars: int) -> None:
        self._text = TextParser()
        self._pptx = PptxParser()
        self._docx = DocxParser()
        self._image = ImageParser(ocr)
        self._pdf = PdfParser(ocr=ocr, ocr_enabled=ocr_enabled, min_chars=min_chars)

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        # Route to the adapter for this (already-sniffed) type. Every adapter returns the
        # same ParsedDocument shape, so the rest of the pipeline doesn't care about format.
        if content_type == "application/pdf":
            return self._pdf.parse(data, content_type)
        if content_type == _PPTX:
            return self._pptx.parse(data, content_type)
        if content_type == _DOCX:
            return self._docx.parse(data, content_type)
        if content_type in ("text/plain", "text/markdown"):
            return self._text.parse(data, content_type)
        if content_type in ("image/png", "image/jpeg"):
            return self._image.parse(data, content_type)
        raise UnsupportedContentType(content_type)  # should never hit (handler sniffs first)
