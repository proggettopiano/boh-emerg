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
    """Compress PDF: try Ghostscript (fast for large/scanned PDFs), fallback to PyMuPDF.
    Always returns (compressed_bytes, was_compressed) — preserves exact signature.
    """
    import subprocess
    import tempfile
    import os
    import sys
    
    # Try Ghostscript if available (much faster for scanned/large PDFs)
    try:
        gs_cmd = 'gswin64c' if sys.platform == 'win32' else 'gs'
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_in, \
             tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_out:
            tmp_in.write(pdf_bytes)
            tmp_in.flush()
            
            cmd = [
                gs_cmd, '-sDEVICE=pdfwrite', '-dCompatibilityLevel=1.4',
                '-dPDFSETTINGS=/ebook', '-dNOPAUSE', '-dQUIET', '-dBATCH',
                f'-sOutputFile={tmp_out.name}', tmp_in.name
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            
            if result.returncode == 0:
                with open(tmp_out.name, 'rb') as f:
                    gs_bytes = f.read()
                # Cleanup temp files
                try:
                    os.unlink(tmp_in.name)
                    os.unlink(tmp_out.name)
                except:
                    pass
                
                # Return if reduced > 5%
                if len(gs_bytes) < len(pdf_bytes) * 0.95:
                    logger.debug(f"Ghostscript compression: {len(pdf_bytes)} to {len(gs_bytes)} bytes")
                    return gs_bytes, True
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        # Ghostscript not installed or failed — silent fallback to PyMuPDF
        logger.debug(f"Ghostscript unavailable: {e}, using PyMuPDF")
    
    # Fallback PyMuPDF (original logic)
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
