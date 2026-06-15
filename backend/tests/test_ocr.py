import shutil

import pytest
from app.rag.ocr import TesseractOcr
from PIL import Image, ImageDraw


def test_tesseract_reads_generated_text_image():
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")
    img = Image.new("RGB", (220, 60), "white")
    ImageDraw.Draw(img).text((10, 20), "HELLO", fill="black")
    text = TesseractOcr(language="eng").extract_text(img)
    assert "HELLO" in text.upper()
