"""PDF text extraction (native text only, no OCR)."""
import logging
from typing import List, Tuple
import re
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Regex per accordi comuni (italiani/internazionali)
# Esempi: Do, Re, Mi, Fa, Sol, La, Si, C, D, E, F, G, A, B, Do7, Rem, Do#, Sib, etc.
CHORD_REGEX = re.compile(r"\b([A-G]|Do|Re|Mi|Fa|Sol|La|Si)(m|maj|min|aug|dim|sus|add)?(2|4|5|6|7|9|11|13)?(#|b)?\b", re.IGNORECASE)

def extract_chords(text: str) -> List[str]:
    """Estrae accordi unici da una stringa di testo."""
    matches = CHORD_REGEX.finditer(text)
    chords = set()
    for m in matches:
        chords.add(m.group(0).capitalize())
    return sorted(list(chords))

def extract_pages(pdf_bytes: bytes) -> Tuple[List[str], int, bool, List[str]]:
    """Extract native text from each page and detect chords.
    OCR removed as per new group policy.

    Returns (pages_text, total_pages, used_ocr=False, chords).
    """
    pages_text: List[str] = []
    used_ocr = False
    all_chords = set()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.error(f"Failed to open PDF: {e}")
        raise

    for page_num in range(len(doc)):
        try:
            page = doc[page_num]
            text = page.get_text("text") or ""
            pages_text.append(text.strip())
            # Estrai accordi da ogni pagina
            for c in extract_chords(text):
                all_chords.add(c)
        except Exception as e:
            logger.warning(f"Failed to extract page {page_num + 1}: {e}")
            pages_text.append("")
    total = len(doc)
    doc.close()
    return pages_text, total, used_ocr, sorted(list(all_chords))


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
