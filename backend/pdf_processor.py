"""PDF text extraction logic with local OCR.

Uses RapidOCR as the primary local OCR path and falls back to Tesseract
when the ONNX runtime engine is unavailable or fails.
"""
import base64
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)

_OCR_NOT_CONFIGURED_WARNING_SHOWN = False
_rapidocr_engine = None
MAX_OCR_IMAGE_SIZE = 1400
MAX_PARALLEL_OCR_WORKERS = 3
_timing_lock = threading.Lock()

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


def normalize_pdf_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r", " ").replace("\n", " ")
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\u0300-\u036f]", "", text)
    text = APOSTROPHE_RE.sub("'", text)
    text = re.sub(r"[^A-Za-z0-9\s']+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()

    replacements = {
        r"\bGesu\b": "Gesù",
        r"\bGest\b": "Gesù",
        r"\bDio\b": "Dio",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text


def extract_page_metadata(text: str) -> dict:
    normalized = normalize_pdf_text(text)
    meter = {}
    match = re.search(r"\b(?:cantico|canto|inno|hymn)\s*#?\s*(\d{1,4})\b", normalized, flags=re.IGNORECASE)
    if not match:
        match = re.match(r"^\s*(\d{1,4})\b", normalized)
    if match:
        try:
            meter["cantico"] = int(match.group(1))
        except ValueError:
            pass
    return meter


def _count_text_words(text: str) -> int:
    return len(re.findall(r"\b[A-Za-zÀ-ÿ0-9]{2,}\b", text))


def _record_timing(timings: Dict[str, Any], key: str, value: float) -> None:
    if timings is not None:
        with _timing_lock:
            timings[key] = timings.get(key, 0.0) + value


def _resize_image_for_ocr(img, max_long_side: int = MAX_OCR_IMAGE_SIZE):
    """Resize large OCR images to a bounded long side before inference."""
    if img is None:
        return img
    if max(img.width, img.height) <= max_long_side:
        return img

    scale = max_long_side / max(img.width, img.height)
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    try:
        return img.resize(new_size, resample=Image.LANCZOS)
    except Exception as exc:
        logger.debug("Image resize for OCR failed: %s", exc)
        return img


def _choose_ocr_worker_count(ocr_candidates: List[tuple], page_details: List[Dict[str, Any]]) -> int:
    if not ocr_candidates:
        return 0

    image_pages = sum(1 for p in page_details if p.get("page_images"))
    mixed_pages = sum(1 for p in page_details if p.get("dict_text_blocks", 0) > 0 and p.get("dict_image_blocks", 0) > 0)
    total_pages = len(page_details) if page_details else len(ocr_candidates)

    is_scanned_pdf = image_pages >= max(1, int(total_pages * 0.75))
    if is_scanned_pdf:
        return min(2, len(ocr_candidates), MAX_PARALLEL_OCR_WORKERS)
    if mixed_pages > 0:
        return min(3, len(ocr_candidates), MAX_PARALLEL_OCR_WORKERS)
    return min(1, len(ocr_candidates), MAX_PARALLEL_OCR_WORKERS)


def _is_noisy_page_text(cleaned_text: str) -> bool:
    if not cleaned_text:
        return True
    tokens = [t for t in cleaned_text.split() if t]
    if len(tokens) < 6:
        return True

    short_tokens = sum(1 for t in tokens if len(re.sub(r"[^A-Za-zÀ-ÿ0-9]", "", t)) <= 2)
    if short_tokens / len(tokens) > 0.35:
        return True

    uppercase_tokens = sum(1 for t in tokens if len(t) >= 3 and t.isupper())
    if uppercase_tokens >= 3:
        return True

    nonletter_ratio = sum(1 for t in tokens if re.fullmatch(r"[^A-Za-zÀ-ÿ0-9]+", t))
    if nonletter_ratio / len(tokens) > 0.15:
        return True

    return False


def _choose_page_text(native_text: str, ocr_text: str) -> str:
    cleaned_native = clean_pdf_text(native_text)
    cleaned_ocr = clean_pdf_text(ocr_text)
    if not cleaned_native:
        return cleaned_ocr
    if not cleaned_ocr:
        return cleaned_native

    native_words = _count_text_words(cleaned_native)
    ocr_words = _count_text_words(cleaned_ocr)

    if _is_noisy_page_text(cleaned_native) and not _is_noisy_page_text(cleaned_ocr):
        return cleaned_ocr

    if ocr_words > native_words + 4:
        return cleaned_ocr

    if cleaned_ocr not in cleaned_native:
        return f"{cleaned_native} {cleaned_ocr}".strip()
    return cleaned_native


def _has_boilerplate_text(cleaned_text: str) -> bool:
    if not cleaned_text:
        return False
    lower = cleaned_text.lower()
    boilerplate_keywords = [
        "scarica",
        "clic",
        "convertitore",
        "foto pdf",
        "foto",
        "photo",
        "created by",
        "generated by",
        "watermark",
        "scan",
        "scansione",
        "download",
        "pdf",
        "free",
        "online",
        "preview",
        "document",
        "convert",
    ]
    matches = sum(1 for token in boilerplate_keywords if token in lower)
    if matches >= 2:
        return True

    words = lower.split()
    if not words:
        return False
    boilerplate_word_set = {"scarica", "clic", "convertitore", "foto", "pdf", "photo", "download", "scan", "scansione", "preview", "free"}
    boilerplate_count = sum(1 for w in words if w in boilerplate_word_set)
    return boilerplate_count >= max(2, len(words) // 5)


def _has_useful_page_text(cleaned_text: str) -> bool:
    if not cleaned_text:
        return False
    if len(cleaned_text) < 40:
        return False
    if _has_boilerplate_text(cleaned_text):
        return False
    return _count_text_words(cleaned_text) >= 6


def _page_has_images(page) -> bool:
    try:
        return bool(page.get_images(full=True))
    except Exception:
        return False


def _find_tesseract_binary() -> str:
    """Resolve the Tesseract binary, validating explicit overrides before using them."""
    explicit = os.environ.get("TESSERACT_PATH") or os.environ.get("TESSERACT_CMD")
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    logger.debug(
        "Searching Tesseract: TESSERACT_PATH=%s, TESSERACT_CMD=%s, PATH=%s",
        os.environ.get("TESSERACT_PATH"),
        os.environ.get("TESSERACT_CMD"),
        path_dirs,
    )

    candidates = []

    if explicit:
        candidates.append(explicit)
        expanded = os.path.expanduser(explicit)
        if expanded != explicit:
            candidates.append(expanded)

    for candidate in candidates:
        logger.debug(f"Checking explicit candidate: {candidate}")
        if os.path.isfile(candidate):
            logger.info(f"Found Tesseract at explicit path: {candidate}")
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            logger.info(f"Found Tesseract via which() for explicit path: {resolved}")
            return resolved

    binary_path = shutil.which("tesseract")
    if binary_path:
        logger.info(f"Found Tesseract via which('tesseract'): {binary_path}")
        return binary_path

    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
        "/usr/share/bin/tesseract",
    ]
    for path in possible_paths:
        logger.debug(f"Checking common Tesseract path: {path}")
        if os.path.isfile(path):
            logger.info(f"Found Tesseract at common path: {path}")
            return path

    logger.warning(
        "Tesseract not found. Checked: explicit=%s, which('tesseract')=None, PATH dirs=%s",
        explicit,
        path_dirs,
    )
    return ""

def _create_rapidocr_engine():
    global _rapidocr_engine
    if _rapidocr_engine is not None:
        return _rapidocr_engine

    try:
        from rapidocr import RapidOCR

        _rapidocr_engine = RapidOCR()
        logger.info("RapidOCR engine initialized successfully")
        return _rapidocr_engine
    except Exception as exc:
        logger.warning("RapidOCR unavailable: %s", exc)
        return None


def _extract_text_with_rapidocr(page, timings: Dict[str, Any] = None) -> str:
    start_total = time.perf_counter()
    render_time = 0.0
    infer_time = 0.0
    try:
        import numpy as np
        from PIL import Image

        start = time.perf_counter()
        pix = page.get_pixmap(alpha=False, dpi=300)
        render_time += time.perf_counter() - start

        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img = _resize_image_for_ocr(img, max_long_side=2000)
        arr = np.asarray(img)
        engine = _create_rapidocr_engine()
        if engine is None:
            return ""

        start = time.perf_counter()
        result = engine(arr)
        infer_time += time.perf_counter() - start
        txts = getattr(result, "txts", None)
        if txts is None and isinstance(result, (list, tuple)):
            txts = result

        if isinstance(txts, (list, tuple)):
            text_parts = []
            for item in txts:
                if isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("txt") or ""
                    if text.strip():
                        text_parts.append(text.strip())
            if text_parts:
                return "\n".join(text_parts).strip()

        return ""
    except Exception as exc:
        logger.warning("RapidOCR OCR failed: %s", exc)
        return ""
    finally:
        _record_timing(timings, "rapidocr_render_ms", render_time * 1000.0)
        _record_timing(timings, "rapidocr_infer_ms", infer_time * 1000.0)
        _record_timing(timings, "rapidocr_ms", (time.perf_counter() - start_total) * 1000.0)
        _record_timing(timings, "rapidocr_calls", 1)


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


def _tesseract_ocr_text(page, timings: Dict[str, Any] = None) -> str:
    start_total = time.perf_counter()
    render_time = 0.0
    infer_time = 0.0
    pass_count = 0
    try:
        import pytesseract
        from PIL import Image
        try:
            from PIL import ImageFilter, ImageOps, ImageEnhance
        except Exception:
            ImageFilter = None
            ImageOps = None
            ImageEnhance = None
    except Exception as exc:
        logger.warning("OCR locale non disponibile: %s", exc)
        return ""

    tesseract_cmd = _find_tesseract_binary()
    if not tesseract_cmd:
        _warn_no_ocr_backend()
        logger.warning("Tesseract non trovato: OCR locale non disponibile")
        return ""

    try:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    except Exception:
        try:
            pytesseract.tesseract_cmd = tesseract_cmd
        except Exception:
            pass

    results = []
    try:
        dpis = [150, 300]
        psm_values = [3, 6, 11]

        for dpi in dpis:
            try:
                start = time.perf_counter()
                pix = page.get_pixmap(alpha=False, dpi=dpi)
                render_time += time.perf_counter() - start
            except Exception:
                try:
                    start = time.perf_counter()
                    pix = page.get_pixmap(alpha=False)
                    render_time += time.perf_counter() - start
                except Exception as exc:
                    logger.warning("Failed to rasterize page for OCR: %s", exc)
                    continue

            try:
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            except Exception:
                try:
                    img = Image.frombuffer("RGB", (pix.width, pix.height), pix.samples, "raw", "RGB", 0, 1)
                except Exception as exc:
                    logger.warning("Failed to convert pixmap to Image: %s", exc)
                    continue

            try:
                if ImageOps is not None and ImageFilter is not None and ImageEnhance is not None:
                    gray = ImageOps.grayscale(img)
                    gray = ImageOps.autocontrast(gray)
                    gray = gray.filter(ImageFilter.MedianFilter(size=3))
                    gray = gray.filter(ImageFilter.SHARPEN)
                    enhancer = ImageEnhance.Contrast(gray)
                    gray = enhancer.enhance(1.3)
                else:
                    gray = img
            except Exception as exc:
                logger.debug("Image preprocessing failed: %s", exc)
                gray = img

            for psm in psm_values:
                try:
                    lang = os.environ.get("TESSERACT_LANG", "ita+eng")
                    config = f"--psm {psm} --oem 3"
                    start = time.perf_counter()
                    try:
                        text = pytesseract.image_to_string(gray, lang=lang, config=config)
                    except TypeError:
                        text = pytesseract.image_to_string(gray)
                    infer_time += time.perf_counter() - start
                    pass_count += 1
                    if text:
                        results.append(text)
                except Exception as exc:
                    logger.debug("Tesseract pass failed (dpi=%s psm=%s): %s", dpi, psm, exc)

        if not results:
            return ""

        lines = []
        for r in results:
            for ln in [l.strip() for l in r.splitlines() if l.strip()]:
                found = False
                for i, exist in enumerate(lines):
                    if ln in exist:
                        found = True
                        break
                    if exist in ln:
                        lines[i] = ln
                        found = True
                        break
                if not found:
                    lines.append(ln)

        merged = "\n".join(lines)
        logger.debug("Tesseract OCR passes produced %d results, merged %d lines", len(results), len(lines))
        return merged
    except Exception as exc:
        logger.warning("Tesseract OCR failed: %s", exc)
        return ""
    finally:
        _record_timing(timings, "tesseract_render_ms", render_time * 1000.0)
        _record_timing(timings, "tesseract_infer_ms", infer_time * 1000.0)
        _record_timing(timings, "tesseract_ms", (time.perf_counter() - start_total) * 1000.0)
        _record_timing(timings, "tesseract_passes", pass_count)
        _record_timing(timings, "tesseract_calls", 1)


def _ocr_page_sync(page, timings: Dict[str, Any] = None) -> str:
    return _ocr_page_text(page, timings=timings)


def _ocr_needs_page(page_info: Dict[str, Any]) -> bool:
    return page_info.get("ocr_attempted", False)


def extract_pages(pdf_bytes: bytes, timings: Dict[str, Any] = None) -> Tuple[List[str], List[str], int, bool, List[str]]:
    """Extract text from each page. OCR logic remains fallback-only.
    Returns (pages_text, raw_texts, total_pages, used_ocr, page_labels).
    """
    if timings is not None:
        timings.setdefault("page_details", [])

    pages_text: List[str] = []
    page_labels: List[str] = []
    used_ocr = False
    start_total = time.perf_counter()
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

    raw_texts: List[str] = [""] * len(doc)
    ocr_candidates = []
    pages_text: List[str] = [""] * len(doc)
    page_details: List[Dict[str, Any]] = []

    for page_num in range(len(doc)):
        page_info: Dict[str, Any] = {
            "page": page_num + 1,
            "ocr_attempted": False,
            "ocr_used": False,
            "reason": [],
        }
        try:
            page = doc[page_num]
            start = time.perf_counter()
            raw_text = page.get_text("text") or ""
            _record_timing(timings, "page_text_ms", (time.perf_counter() - start) * 1000.0)

            # Also inspect the structured dict output to detect text vs image blocks.
            start = time.perf_counter()
            try:
                text_dict = page.get_text("dict") or {}
            except Exception:
                text_dict = {}
            blocks = text_dict.get("blocks", []) if isinstance(text_dict, dict) else []
            text_blocks = sum(1 for b in blocks if b.get("type") == 0)
            image_blocks = sum(1 for b in blocks if b.get("type") == 1)

            # Reconstruct text from dict spans (more reliable for block-level detection)
            dict_text_parts = []
            for b in blocks:
                if b.get("type") != 0:
                    continue
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        txt = span.get("text") or ""
                        if txt:
                            dict_text_parts.append(txt)
            dict_text = "\n".join(dict_text_parts).strip()
            dict_cleaned = clean_pdf_text(dict_text)

            # Also check low-level images() as a fallback (may miss inline-painted images)
            try:
                image_xobjs = bool(page.get_images(full=True))
            except Exception:
                image_xobjs = False
            _record_timing(timings, "page_images_ms", (time.perf_counter() - start) * 1000.0)

            # Decide whether page is image-like using both dict blocks and get_images
            # Rules (in order):
            # 1) If dict has significant text blocks, prefer native text and skip OCR.
            # 2) If no text blocks and image blocks exist, treat as image -> OCR.
            # 3) If both exist, consider mixed: use native if native text quality passes threshold, otherwise OCR.
            cleaned = clean_pdf_text(raw_text)
            # Use dict_cleaned when available to evaluate native text quality.
            native_text_for_quality = dict_cleaned if dict_cleaned else cleaned
            has_significant_text_blocks = text_blocks > 0 and _has_useful_page_text(native_text_for_quality)
            has_any_text_blocks = text_blocks > 0
            has_any_image_blocks = image_blocks > 0 or image_xobjs
            # page_images indicates whether OCR should be considered because page is image-like
            if has_significant_text_blocks:
                page_images = False
            elif not has_any_text_blocks and has_any_image_blocks:
                page_images = True
            elif has_any_text_blocks and has_any_image_blocks:
                # Mixed page: choose native if quality sufficient
                page_images = not _has_useful_page_text(native_text_for_quality)
            else:
                # Fallback to previous heuristic using raw cleaned text
                page_images = _page_has_images(page)

            word_count = _count_text_words(cleaned)
            page_info.update({
                "raw_length": len(raw_text),
                "clean_length": len(cleaned),
                "word_count": word_count,
                "page_images": page_images,
                "is_noisy": _is_noisy_page_text(cleaned),
                "dict_text_blocks": text_blocks,
                "dict_image_blocks": image_blocks,
            })
            
            needs_ocr = (
                page_images                    # Has images that need OCR
                or len(cleaned) < 40           # Almost empty page
                or word_count < 3              # No meaningful text
                or _is_noisy_page_text(cleaned) # Noisy native extraction may be improved by OCR
            )
            
            if needs_ocr:
                page_info["ocr_attempted"] = True
                if page_images:
                    page_info["reason"].append("images")
                if len(cleaned) < 40:
                    page_info["reason"].append("short_text")
                if word_count < 3:
                    page_info["reason"].append("no_words")
                if page_info["is_noisy"]:
                    page_info["reason"].append("noisy_text")

                logger.info(
                    "Page %s: OCR attempt - chars=%s words=%s images=%s reason=%s",
                    page_num + 1,
                    len(cleaned),
                    word_count,
                    page_images,
                    "+".join(page_info["reason"]) or "unknown",
                )
                ocr_candidates.append((page_num, page, cleaned, page_info))

            pages_text[page_num] = cleaned
            raw_texts[page_num] = raw_text
            page_details.append(page_info)
            if labels and page_num < len(labels) and labels[page_num] is not None:
                page_labels.append(labels[page_num])
            else:
                page_labels.append(str(page_num + 1))
        except Exception as e:
            logger.warning(f"Failed to extract page {page_num + 1}: {e}")
            pages_text[page_num] = ""
            raw_texts[page_num] = ""
            page_details.append(page_info)
            if labels and page_num < len(labels) and labels[page_num] is not None:
                page_labels.append(labels[page_num])
            else:
                page_labels.append(str(page_num + 1))

    if ocr_candidates:
        max_workers = _choose_ocr_worker_count(ocr_candidates, page_details)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_page = {
                executor.submit(_ocr_page_sync, page, timings): (page_num, cleaned, page_info)
                for page_num, page, cleaned, page_info in ocr_candidates
            }
            for future in as_completed(future_to_page):
                page_num, cleaned, page_info = future_to_page[future]
                ocr_text = ""
                ocr_ms = 0.0
                try:
                    ocr_start = time.perf_counter()
                    ocr_text = future.result()
                    ocr_ms = (time.perf_counter() - ocr_start) * 1000.0
                except Exception as exc:
                    logger.warning("Page %s OCR failed in thread: %s", page_num + 1, exc)

                logger.info("Page %s OCR time: %.0f ms", page_num + 1, ocr_ms)
                if timings is not None:
                    _record_timing(timings, "page_ocr_ms", ocr_ms)

                if ocr_text:
                    chosen = _choose_page_text(cleaned, ocr_text)
                    if chosen != cleaned:
                        used_ocr = True
                        page_info["ocr_used"] = True
                        logger.info(
                            "Page %s: OCR yielded better text (native %d words, ocr %d words, final %d words)",
                            page_num + 1,
                            _count_text_words(cleaned),
                            _count_text_words(clean_pdf_text(ocr_text)),
                            _count_text_words(chosen),
                        )
                    pages_text[page_num] = chosen
                page_info["ocr_ms"] = ocr_ms

    if timings is not None:
        for page_info in page_details:
            timings["page_details"].append(page_info)
            _record_timing(timings, "pages_with_ocr", 1 if page_info["ocr_attempted"] else 0)
            _record_timing(timings, "pages_with_ocr_used", 1 if page_info["ocr_used"] else 0)
            if not page_info["ocr_attempted"] and page_info.get("page_images"):
                _record_timing(timings, "image_pages_skipped_ocr", 1)
            if (
                page_info["ocr_attempted"]
                and not page_info.get("page_images")
                and not page_info.get("is_noisy")
                and page_info.get("clean_length", 0) >= 40
                and page_info.get("word_count", 0) >= 3
            ):
                _record_timing(timings, "unnecessary_ocr_pages", 1)
            _record_timing(timings, "page_count", 1)
            if page_info["reason"]:
                for reason in page_info["reason"]:
                    key = f"reason_{reason}"
                    _record_timing(timings, key, 1)

    total = len(doc)
    doc.close()
    if timings is not None:
        _record_timing(timings, "extract_pages_ms", (time.perf_counter() - start_total) * 1000.0)
    return pages_text, raw_texts, total, used_ocr, page_labels


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
