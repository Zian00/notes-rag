import io
import zipfile

from app.utils.files import sanitize_filename, sniff_content_type

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF_MAGIC = b"%PDF-1.7\n" + b"rest"


def _ooxml(marker_dir: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(f"{marker_dir}/presentation.xml", "x")
    return buf.getvalue()


def test_sniff_pdf_png_jpeg():
    assert sniff_content_type("a.pdf", PDF_MAGIC) == "application/pdf"
    assert sniff_content_type("a.png", PNG_MAGIC) == "image/png"
    assert sniff_content_type("a.jpg", JPEG_MAGIC) == "image/jpeg"


def test_sniff_pptx_vs_docx():
    pptx = sniff_content_type("a.pptx", _ooxml("ppt"))
    docx = sniff_content_type("a.docx", _ooxml("word"))
    assert pptx.endswith("presentationml.presentation")
    assert docx.endswith("wordprocessingml.document")


def test_sniff_text_and_markdown_by_extension():
    assert sniff_content_type("notes.txt", b"plain text") == "text/plain"
    assert sniff_content_type("notes.md", b"# Heading") == "text/markdown"


def test_sniff_rejects_unknown():
    assert sniff_content_type("evil.exe", b"MZ\x90\x00") is None
    assert sniff_content_type("fake.txt", b"\xff\xfe\x00\x01") is None


def test_sanitize_filename_strips_paths_and_bad_chars():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("my notes/Lecture 3.pdf") == "Lecture 3.pdf"
    assert sanitize_filename("") == "upload"
