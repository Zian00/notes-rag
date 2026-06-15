import io
import os
import zipfile

_PDF = "application/pdf"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TXT = "text/plain"
_MD = "text/markdown"
_PNG = "image/png"
_JPEG = "image/jpeg"


def sniff_content_type(filename: str, data: bytes) -> str | None:
    """Determine the real content type from magic bytes (not the client's label).

    Returns a canonical MIME string, or None if unsupported. OOXML files (PPTX/DOCX)
    are ZIP archives, so we inspect the archive entries to tell them apart. TXT/MD have
    no magic number, so we accept them by extension only if the bytes decode as UTF-8.
    """
    if data.startswith(b"%PDF"):
        return _PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG
    if data.startswith(b"\xff\xd8\xff"):
        return _JPEG
    if data.startswith(b"PK\x03\x04"):
        return _sniff_ooxml(data)

    ext = os.path.splitext(filename)[1].lower()
    if ext in (".txt", ".md"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return _MD if ext == ".md" else _TXT
    return None


def _sniff_ooxml(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return None
    if any(n.startswith("ppt/") for n in names):
        return _PPTX
    if any(n.startswith("word/") for n in names):
        return _DOCX
    return None


def sanitize_filename(filename: str) -> str:
    """Strip directory components and return a safe base name (fallback 'upload')."""
    base = os.path.basename(filename.replace("\\", "/")).strip()
    return base or "upload"
