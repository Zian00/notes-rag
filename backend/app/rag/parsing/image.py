import io

from PIL import Image

from app.rag.ocr import OcrProvider
from app.rag.types import ParsedDocument, Segment


class ImageParser:
    """Runs the whole image through OCR as a single segment (page_number = 1)."""

    def __init__(self, ocr: OcrProvider) -> None:
        self._ocr = ocr

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        image = Image.open(io.BytesIO(data))
        text = self._ocr.extract_text(image)
        return ParsedDocument(segments=[Segment(text=text, page_number=1)], page_count=1)
