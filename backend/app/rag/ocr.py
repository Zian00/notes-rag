from abc import ABC, abstractmethod

import pytesseract
from PIL import Image


class OcrProvider(ABC):
    """Port: extract text from a single image."""

    @abstractmethod
    def extract_text(self, image: Image.Image) -> str: ...


class TesseractOcr(OcrProvider):
    """Local Tesseract OCR adapter. Requires the `tesseract` binary on the host."""

    def __init__(self, language: str = "eng", cmd: str | None = None) -> None:
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self._language = language

    def extract_text(self, image: Image.Image) -> str:
        return pytesseract.image_to_string(image, lang=self._language)
