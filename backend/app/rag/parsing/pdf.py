import io

from pdf2image import convert_from_bytes
from pypdf import PdfReader

from app.rag.ocr import OcrProvider
from app.rag.types import ParsedDocument, Segment


class PdfParser:
    """Extracts text per page with pypdf; any page below `min_chars` is rasterized
    (pdf2image, requires poppler) and OCR'd. Handles scanned + mixed PDFs."""

    def __init__(self, ocr: OcrProvider, ocr_enabled: bool, min_chars: int) -> None:
        self._ocr = ocr
        self._ocr_enabled = ocr_enabled
        self._min_chars = min_chars

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        reader = PdfReader(io.BytesIO(data))
        segments: list[Segment] = []
        # One segment per page. We try the embedded text layer first (fast, exact).
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            # A near-empty page usually means it's a scanned image with no text layer →
            # fall back to OCR for just that page. This handles mixed text/scanned PDFs.
            if len(text) < self._min_chars and self._ocr_enabled:
                text = self._ocr_page(data, index).strip() or text
            segments.append(Segment(text=text, page_number=index))
        return ParsedDocument(segments=segments, page_count=len(reader.pages))

    def _ocr_page(self, data: bytes, page_number: int) -> str:
        # Rasterize a single page to an image (needs poppler), then OCR it.
        images = convert_from_bytes(data, first_page=page_number, last_page=page_number)
        if not images:
            return ""
        return self._ocr.extract_text(images[0])
