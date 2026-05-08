"""PDF text extraction with OCR fallback for scanned PDFs."""
import logging
import io
from typing import List, Tuple
import fitz  # PyMuPDF
from PIL import Image
import pytesseract

logger = logging.getLogger(__name__)

OCR_LANGS = "eng+ita"
MIN_CHARS_PER_PAGE = 25  # below this, treat page as scanned and run OCR


def extract_pages(pdf_bytes: bytes) -> Tuple[List[str], int, bool]:
    """Extract text from each page. Falls back to OCR for image-only pages.

    Returns (pages_text, total_pages, used_ocr).
    """
    pages_text: List[str] = []
    used_ocr = False
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.error(f"Failed to open PDF: {e}")
        raise

    for page_num in range(len(doc)):
        try:
            page = doc[page_num]
            text = page.get_text("text") or ""
            text = text.strip()
            if len(text) < MIN_CHARS_PER_PAGE:
                # OCR fallback
                try:
                    pix = page.get_pixmap(dpi=200)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    ocr_text = pytesseract.image_to_string(img, lang=OCR_LANGS) or ""
                    if len(ocr_text.strip()) > len(text):
                        text = ocr_text.strip()
                        used_ocr = True
                except Exception as e:
                    logger.warning(f"OCR failed on page {page_num + 1}: {e}")
            pages_text.append(text)
        except Exception as e:
            logger.warning(f"Failed to extract page {page_num + 1}: {e}")
            pages_text.append("")
    total = len(doc)
    doc.close()
    return pages_text, total, used_ocr


def compress_pdf(pdf_bytes: bytes) -> Tuple[bytes, bool]:
    """Try to compress a PDF using PyMuPDF deflate. Returns (bytes, was_compressed)."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        out = doc.tobytes(garbage=4, deflate=True, clean=True)
        doc.close()
        if len(out) < len(pdf_bytes) * 0.95:
            return out, True
        return pdf_bytes, False
    except Exception as e:
        logger.warning(f"Compression failed: {e}")
        return pdf_bytes, False


def make_snippet(text: str, query: str, length: int = 200) -> str:
    """Return a snippet of `text` around the first occurrence of `query`."""
    if not text:
        return ""
    lower = text.lower()
    q = query.lower().strip()
    if not q:
        return text[:length]
    idx = lower.find(q)
    if idx < 0:
        # try first word
        first = q.split()[0] if q.split() else q
        idx = lower.find(first)
    if idx < 0:
        return text[:length].strip()
    start = max(0, idx - length // 3)
    end = min(len(text), idx + length)
    snippet = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "… " + snippet
    if end < len(text):
        snippet = snippet + " …"
    return snippet
