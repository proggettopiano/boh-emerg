"""PDF text extraction logic based on stable-pdf-v1 (NO OCR)."""
import logging
import re
import unicodedata
from typing import List, Tuple
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

MUSIC_SYMBOL_RE = re.compile(r"[\u0000-\u001F\u007F]+")


def clean_pdf_text(text: str) -> str:
    """Keep readable words and numbers, remove chords, notation symbols and glued artifacts."""
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\u00a0", " ")
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))

    # Drop music notation, chord symbols, OCR artifacts and punctuation.
    text = re.sub(r"[\u0000-\u001F\u007F]", " ", text)
    text = re.sub(r"[œŒ˙…]+", " ", text)
    text = re.sub(r"(?<![A-Za-zÀ-ÿ])(?:[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:/[A-G](?:#|b)?)?)(?![A-Za-zÀ-ÿ])", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^A-Za-z0-9À-ÿ\s]+", " ", text)

    # Split only likely OCR-glued words such as "GesùCristo".
    text = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý][a-zà-ÿ])", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_pages(pdf_bytes: bytes) -> Tuple[List[str], int, bool, List[str]]:
    """Extract text from each page. OCR logic removed for stability.
    This follows the stable-pdf-v1 structure but removes the pytesseract dependency.

    Returns (pages_text, total_pages, used_ocr, page_labels).
    """
    pages_text: List[str] = []
    page_labels: List[str] = []
    used_ocr = False
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.error(f"Failed to open PDF: {e}")
        raise

    labels = None
    try:
        labels = doc.get_page_labels()
    except Exception:
        labels = None

    for page_num in range(len(doc)):
        try:
            page = doc[page_num]
            text = page.get_text("text") or ""
            pages_text.append(clean_pdf_text(text))
        except Exception as e:
            logger.warning(f"Failed to extract page {page_num + 1}: {e}")
            pages_text.append("")

        if labels and page_num < len(labels) and labels[page_num] is not None:
            page_labels.append(labels[page_num])
        else:
            page_labels.append(str(page_num + 1))
            
    total = len(doc)
    doc.close()
    return pages_text, total, used_ocr, page_labels


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
