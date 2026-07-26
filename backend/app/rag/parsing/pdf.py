import fitz  # PyMuPDF — used directly only to open the doc and check per-page text length
import pymupdf4llm  # for the OCR-fallback decision; pymupdf4llm does the actual Markdown extraction
from pdf2image import convert_from_bytes
from PIL.Image import Image

from app.rag.ocr import OcrProvider
from app.rag.parsing.heading_split import split_markdown_by_headings
from app.rag.types import ParsedDocument, Segment

# The installed pymupdf4llm (1.28.0) defaults to a "layout" backend (the bundled
# pymupdf-layout package) when available, which infers headings from an ML layout
# model instead of the classic font-size heuristic the original design assumed.
# VERIFIED DURING IMPLEMENTATION: that layout backend has a state-corruption bug —
# calling `pymupdf4llm.to_markdown()` a second time in the same process (i.e. on
# the second PDF a running server parses) silently returns empty/wrong text for
# documents it had previously parsed correctly in isolation. This is unacceptable
# for a long-lived FastAPI process. Forcing the classic backend (`use_layout(False)`)
# does not have this bug (verified across repeated calls on distinct documents) and
# still infers '#'/'##'/'###' Markdown headers from font size/weight, matching what
# `split_markdown_by_headings` expects. See Task 11 report for the full repro.
pymupdf4llm.use_layout(False)


class PdfParser:
    """Extracts Markdown (with '#' headings inferred from font size/weight via
    pymupdf4llm's classic, non-layout backend — see the module-level note on why
    layout mode is force-disabled) per page, then splits each page's Markdown on
    those headings the same way TextParser does — replacing the old page-only,
    heading-blind pypdf-based segmentation. Any page pymupdf4llm can't extract
    enough embedded text from (a scanned page) is OCR'd separately (pdf2image,
    requires poppler, via the injected OcrProvider) and appended as its own
    heading-less segment, same fallback role the old parser's per-page OCR played.

    Page-tracking note: we pass `page_chunks=True`, which returns one dict per
    page (`text` plus `metadata["page"]`, 1-indexed) instead of one whole-document
    string. This lets every heading-derived segment carry an exact page number.
    The tradeoff: the heading stack does not carry across a page boundary, so if a
    section's body continues onto the next page under no new heading, that
    continuation becomes its own heading-less (`section=None`) segment for that
    page rather than being merged into the prior section. This is an accepted,
    documented approximation — see Task 11 report.
    """

    def __init__(self, ocr: OcrProvider, ocr_enabled: bool, min_chars: int) -> None:
        self._ocr = ocr
        self._ocr_enabled = ocr_enabled
        self._min_chars = min_chars

    def parse(self, data: bytes, content_type: str) -> ParsedDocument:
        doc = fitz.open(stream=data, filetype="pdf")
        page_count = doc.page_count

        # Pages pymupdf4llm likely can't get usable text from (near-empty embedded
        # text layer — same heuristic the old parser used) get OCR'd separately.
        scanned_pages = {
            i for i in range(page_count) if len((doc[i].get_text() or "").strip()) < self._min_chars
        }

        page_chunks = pymupdf4llm.to_markdown(doc, page_chunks=True)

        segments: list[Segment] = []
        for chunk in page_chunks:
            page_number = chunk["metadata"]["page"]
            # Scanned pages produce little/no Markdown text here; skip straight to
            # the OCR fallback below rather than emitting an empty structural segment.
            if (page_number - 1) in scanned_pages:
                continue
            segments.extend(split_markdown_by_headings(chunk["text"], page_number=page_number))

        if self._ocr_enabled:
            for page_index in sorted(scanned_pages):
                image = _render_page(data, page_index)
                ocr_text = self._ocr.extract_text(image).strip()
                if ocr_text:
                    segments.append(Segment(text=ocr_text, page_number=page_index + 1))

        return ParsedDocument(segments=segments, page_count=page_count)


def _render_page(data: bytes, page_index: int) -> Image:
    images = convert_from_bytes(data, first_page=page_index + 1, last_page=page_index + 1)
    return images[0]
