"""PDF text extraction logic with safe OCR fallback.

Uses Google Vision OCR when configured in production, and falls back to local
Tesseract only if cloud OCR is not available.
"""
import base64
import io
import json
import logging
import os
import re
import shutil
import unicodedata
from typing import List, Tuple
import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)

_OCR_NOT_CONFIGURED_WARNING_SHOWN = False

MUSIC_SYMBOL_RE = re.compile(r"[\u0000-\u001F\u007F]+")
APOSTROPHE_RE = re.compile(r"[’‘`]")
DECORATIVE_NUMBER_RE = re.compile(r"~\s*\d+\s*~")


def clean_pdf_text(text: str) -> str:
    """Keep readable words and numbers, remove chords, notation symbols and glued artifacts."""
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\u00a0", " ")
    text = text.replace("\r", " ").replace("\n", " ")
    text = APOSTROPHE_RE.sub("'", text)
    text = DECORATIVE_NUMBER_RE.sub(" ", text)
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))

    # Remove music notation, note/chord tokens and OCR noise.
    text = re.sub(r"[\u0000-\u001F\u007F]", " ", text)
    text = re.sub(r"[œŒ˙…]+", " ", text)
    text = re.sub(r"\b(?:DO|RE|MI|FA|SOL|LA|SI)(?:[#b]|[-/][A-Z0-9#b]+|\d+|maj|min|m|dim|aug|sus|add|7|9|11|13)*\b", " ", text)
    text = re.sub(r"(?<![A-Za-zÀ-ÿ])(?:[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:/[A-G](?:#|b)?\d*)?)(?![A-Za-zÀ-ÿ])", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[A-Za-zÀ-ÿ])\s*[-–—]\s*(?=[A-Za-zÀ-ÿ])", "", text)
    text = re.sub(r"[^A-Za-z0-9À-ÿ\s']+", " ", text, flags=re.UNICODE)

    # Split likely OCR-glued words such as "GesùCristo" or words broken across lines.
    text = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý][a-zà-ÿ])", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _find_tesseract_binary() -> str:
    # Prefer explicit environment override, otherwise fall back to discovery.
    explicit = os.environ.get("TESSERACT_PATH") or os.environ.get("TESSERACT_CMD")
    if explicit:
        return explicit

    binary_path = shutil.which("tesseract")
    if binary_path:
        return binary_path

    # Windows common install locations (user may have installed without PATH).
    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
        "/usr/share/bin/tesseract",
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            return path
    return ""


def _get_google_vision_auth() -> Tuple[str, str]:
    """Return a tuple (mode, value) for Google Vision HTTP auth.
    mode is either 'key' or 'bearer'.
    """
    api_key = os.environ.get("GOOGLE_VISION_API_KEY") or os.environ.get("GOOGLE_CLOUD_VISION_API_KEY")
    if api_key:
        source = "GOOGLE_VISION_API_KEY" if os.environ.get("GOOGLE_VISION_API_KEY") else "GOOGLE_CLOUD_VISION_API_KEY"
        logger.info("Google Vision auth: using API key from %s", source)
        return "key", api_key

    # Support GOOGLE_APPLICATION_CREDENTIALS as either a service account JSON path or an API key.
    adc_value = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if adc_value:
        if adc_value.startswith("AIza"):
            logger.info("Google Vision auth: using API key from GOOGLE_APPLICATION_CREDENTIALS")
            return "key", adc_value
        if os.path.isfile(adc_value):
            try:
                with open(adc_value, "r", encoding="utf-8") as f:
                    creds_json = json.load(f)
                client_email = creds_json.get("client_email")
                private_key = creds_json.get("private_key")
                if client_email and private_key:
                    logger.info("Google Vision auth: using ADC service account from GOOGLE_APPLICATION_CREDENTIALS path=%s", adc_value)
                    return "adc", adc_value
            except Exception as exc:
                logger.warning("Failed to read GOOGLE_APPLICATION_CREDENTIALS: %s", exc)

    try:
        import google.auth
        from google.auth.transport.requests import Request as GoogleRequest

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not creds.valid:
            creds.refresh(GoogleRequest())
        if creds and creds.token:
            return "bearer", creds.token
    except Exception as exc:
        logger.debug("Google Vision auth unavailable: %s", exc)

    return "", ""


def _extract_text_with_google_vision(page) -> str:
    auth_type, auth_value = _get_google_vision_auth()
    if not auth_type:
        return ""

    try:
        from PIL import Image
        pix = page.get_pixmap(alpha=False, dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_content = base64.b64encode(buf.getvalue()).decode("utf-8")

        url = "https://vision.googleapis.com/v1/images:annotate"
        params = {"requests": [{
            "image": {"content": image_content},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
        }]}
        headers = {"Content-Type": "application/json"}
        if auth_type == "key":
            safe_key = f"***{auth_value[-6:]}" if len(auth_value) > 6 else "***"
            url = f"{url}?key={auth_value}"
            logger.info("Google Vision request: sending API key auth (key suffix=%s)", safe_key)
        elif auth_type == "adc":
            # Use OAuth2 access token generated from service account JSON.
            from google.oauth2.service_account import Credentials as ServiceAccountCredentials
            from google.auth.transport.requests import Request as GoogleRequest

            creds = ServiceAccountCredentials.from_service_account_file(auth_value, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(GoogleRequest())
            if not creds.token:
                raise RuntimeError("Failed to obtain access token from service account credentials")
            headers["Authorization"] = f"Bearer {creds.token}"
            logger.info("Google Vision request: sending ADC bearer auth from %s", auth_value)
        else:
            headers["Authorization"] = f"Bearer {auth_value}"
            logger.info("Google Vision request: sending bearer token auth")

        request_url = url if auth_type != "key" else f"https://vision.googleapis.com/v1/images:annotate?key={safe_key}"
        logger.info("Google Vision request URL: %s", request_url)
        resp = httpx.post(url, json=params, headers=headers, timeout=60.0)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as http_exc:
            logger.warning("Google Vision HTTP error %s: %s", resp.status_code, resp.text[:1000])
            raise
        response_data = resp.json()
        responses = response_data.get("responses", [])
        if not responses:
            return ""
        response = responses[0]
        text = response.get("fullTextAnnotation", {}).get("text")
        if not text:
            text = (response.get("textAnnotations", [{}])[0].get("description") or "")
        return text or ""
    except Exception as exc:
        logger.warning("Google Vision OCR failed: %s", exc)
        if auth_type == "key":
            adc_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if adc_path and os.path.isfile(adc_path):
                logger.info("Falling back to GOOGLE_APPLICATION_CREDENTIALS after Vision API key failure")
                try:
                    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
                    from google.auth.transport.requests import Request as GoogleRequest

                    creds = ServiceAccountCredentials.from_service_account_file(adc_path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                    creds.refresh(GoogleRequest())
                    if creds.token:
                        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {creds.token}"}
                        resp = httpx.post(url, json=params, headers=headers, timeout=60.0)
                        resp.raise_for_status()
                        response_data = resp.json()
                        responses = response_data.get("responses", [])
                        if not responses:
                            return ""
                        response = responses[0]
                        text = response.get("fullTextAnnotation", {}).get("text")
                        if not text:
                            text = (response.get("textAnnotations", [{}])[0].get("description") or "")
                        return text or ""
                except Exception as exc2:
                    logger.warning("Google Vision OCR ADC fallback failed: %s", exc2)
        if auth_type == "key" and not _has_google_vision_auth():
            logger.warning("Google Vision key fallita e GOOGLE_APPLICATION_CREDENTIALS non valida o non presente.")
        return ""


def _warn_no_ocr_backend():
    global _OCR_NOT_CONFIGURED_WARNING_SHOWN
    if not _OCR_NOT_CONFIGURED_WARNING_SHOWN:
        logger.warning(
            "Nessun backend OCR locale disponibile: installare Tesseract o impostare TESSERACT_PATH/TESSERACT_CMD."
        )
        _OCR_NOT_CONFIGURED_WARNING_SHOWN = True


def _has_google_vision_auth() -> bool:
    return bool(
        os.environ.get("GOOGLE_VISION_API_KEY")
        or os.environ.get("GOOGLE_CLOUD_VISION_API_KEY")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    )


def _ocr_page_text(page) -> str:
    cloud_text = _extract_text_with_google_vision(page)
    if cloud_text:
        return cloud_text

    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:
        logger.warning("OCR non disponibile per la pagina: %s", exc)
        return ""

    tesseract_cmd = _find_tesseract_binary()
    if not tesseract_cmd:
        if _has_google_vision_auth():
            logger.warning(
                "Cloud OCR configurata ma non disponibile (Google Vision fallito); "
                "Tesseract non trovato nel PATH o in TESSERACT_PATH/TESSERACT_CMD."
            )
        else:
            _warn_no_ocr_backend()
            logger.warning("Tesseract binary non trovato nel PATH o in TESSERACT_PATH/TESSERACT_CMD")
        return ""

    try:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    except Exception:
        # Some pytesseract versions may expose the command setter differently.
        try:
            pytesseract.tesseract_cmd = tesseract_cmd
        except Exception:
            pass

    try:
        pix = page.get_pixmap(alpha=False, dpi=150)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(img) or ""
    except Exception as exc:
        logger.warning("OCR fallback pagina fallito: %s", exc)
        return ""


def extract_pages(pdf_bytes: bytes) -> Tuple[List[str], int, bool, List[str]]:
    """Extract text from each page. OCR logic remains fallback-only.
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
            cleaned = clean_pdf_text(text)
            if len(cleaned) < 40:
                ocr_text = _ocr_page_text(page)
                if ocr_text:
                    cleaned_ocr = clean_pdf_text(ocr_text)
                    if len(cleaned_ocr) > len(cleaned):
                        cleaned = cleaned_ocr
                        used_ocr = True
            pages_text.append(cleaned)
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
