import argparse
import io
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

sys.path.insert(0, r"C:\Users\miche\.claude\boh-emerg\.venv-1\Lib\site-packages")

os.environ.setdefault("TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
logging.getLogger().setLevel(logging.CRITICAL)
logging.getLogger("rapidocr").setLevel(logging.CRITICAL)

import pdf_processor


OUTPUT_DIR = Path(__file__).parent / "bench_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


@contextmanager
def temp_env(overrides):
    original = {}
    missing = set()
    for key, value in overrides.items():
        if key in os.environ:
            original[key] = os.environ[key]
        else:
            missing.add(key)
        os.environ[key] = str(value)
    try:
        yield
    finally:
        for key in overrides:
            if key in original:
                os.environ[key] = original[key]
            elif key in missing and key in os.environ:
                del os.environ[key]


def make_image_only_pdf() -> bytes:
    img = Image.new("RGB", (900, 260), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
    except Exception:
        font = ImageFont.load_default()

    draw.text((40, 40), "REAL OCR TEST IMAGE PAGE", fill="black", font=font)
    draw.text((40, 110), "Canto 542 - Gesu Cristo e la pace", fill="black", font=font)
    draw.text((40, 170), "Benchmark per PDF piccolo di sole immagini", fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=letter)
    c.drawInlineImage(Image.open(io.BytesIO(img_bytes)), 40, 330, width=500, height=200)
    c.showPage()
    c.save()
    return pdf_buf.getvalue()


def make_long_pdf(page_count: int = 54) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, page_count + 1):
        img = Image.new("RGB", (520, 170), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        draw.text((20, 20), f"OCR PAGE {i}", fill="black", font=font)
        draw.text((20, 50), "Canto 542 - Gesu Cristo e la pace", fill="black", font=font)
        draw.text((20, 80), "Benchmark real PDF lungo con immagini", fill="black", font=font)
        draw.text((20, 110), "parola chiave benchmark real OCR", fill="black", font=font)
        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        c.drawInlineImage(Image.open(io.BytesIO(img_buf.getvalue())), 40, 420, width=520, height=170)
        c.showPage()
    c.save()
    return buf.getvalue()


def run_case(name: str, pdf_bytes: bytes, repeats: int = 1):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    doc.close()

    def measure_rapid():
        start = time.perf_counter()
        pages, _, _, used_ocr, _ = pdf_processor.extract_pages(pdf_bytes)
        return (time.perf_counter() - start) * 1000.0, used_ocr, pages

    def measure_tesseract_only():
        original = pdf_processor._extract_text_with_rapidocr
        try:
            pdf_processor._extract_text_with_rapidocr = lambda page: ""
            start = time.perf_counter()
            pages, _, _, used_ocr, _ = pdf_processor.extract_pages(pdf_bytes)
            return (time.perf_counter() - start) * 1000.0, used_ocr, pages
        finally:
            pdf_processor._extract_text_with_rapidocr = original

    cold_rapid_ms, used_ocr_rapid, rapid_pages = measure_rapid()
    cold_tess_ms, used_ocr_tess, tess_pages = measure_tesseract_only()

    rapid_times = [measure_rapid()[0] for _ in range(repeats)]
    tess_times = [measure_tesseract_only()[0] for _ in range(repeats)]

    rapid_text = "\n".join(p for p in rapid_pages if p).lower()
    contains_expected = all(token in rapid_text for token in ["canto", "gesu", "pace", "benchmark"])

    return {
        "name": name,
        "pages": page_count,
        "cold_rapid_ms": round(cold_rapid_ms, 1),
        "cold_tesseract_ms": round(cold_tess_ms, 1),
        "warm_rapid_avg_ms": round(sum(rapid_times) / len(rapid_times), 1),
        "warm_tesseract_avg_ms": round(sum(tess_times) / len(tess_times), 1),
        "rapid_words": len([w for p in rapid_pages for w in p.split() if len(w) > 2]),
        "tesseract_words": len([w for p in tess_pages for w in p.split() if len(w) > 2]),
        "rapid_has_expected": contains_expected,
        "used_ocr_rapid": used_ocr_rapid,
        "used_ocr_tesseract": used_ocr_tess,
    }


def run_tesseract_profile(name: str, pdf_bytes: bytes, profile: dict, repeats: int = 1):
    def measure_once():
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            start = time.perf_counter()
            text = pdf_processor._tesseract_ocr_text(page, page_num=0)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            cleaned_text = pdf_processor.clean_pdf_text(text)
            return elapsed_ms, text, cleaned_text
        finally:
            doc.close()

    with temp_env(profile):
        timings = [measure_once() for _ in range(repeats)]

    elapsed_values = [item[0] for item in timings]
    texts = [item[1] for item in timings]
    cleaned_text = timings[0][2] if timings else ""
    return {
        "name": name,
        "profile": profile,
        "elapsed_ms": round(sum(elapsed_values) / len(elapsed_values), 1) if elapsed_values else 0.0,
        "words": len([w for w in cleaned_text.split() if len(w) > 2]),
        "has_expected": all(token in cleaned_text.lower() for token in ["canto", "gesu", "pace", "benchmark"]),
        "text_preview": texts[0][:120] if texts else "",
    }


def main():
    parser = argparse.ArgumentParser(description="Measure real OCR timings on image-based PDFs")
    parser.add_argument("--case", choices=["image_only_pdf", "long_54_pages_pdf"], default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--compare-tesseract-profiles", action="store_true")
    args = parser.parse_args()

    cases = [
        ("image_only_pdf", make_image_only_pdf()),
        ("long_54_pages_pdf", make_long_pdf(54)),
    ]
    if args.case:
        cases = [next(item for item in cases if item[0] == args.case)]

    for name, pdf_bytes in cases:
        out_path = OUTPUT_DIR / f"{name}.pdf"
        out_path.write_bytes(pdf_bytes)
        if args.compare_tesseract_profiles:
            profiles = [
                {"TESSERACT_LANG": "ita", "OCR_PRIMARY_PSM": 6, "OCR_PRIMARY_OEM": 3},
                {"TESSERACT_LANG": "ita", "OCR_PRIMARY_PSM": 11, "OCR_PRIMARY_OEM": 3},
                {"TESSERACT_LANG": "ita", "OCR_PRIMARY_PSM": 6, "OCR_PRIMARY_OEM": 1},
            ]
            for profile in profiles:
                result = run_tesseract_profile(name, pdf_bytes, profile, repeats=args.repeats)
                print("BENCHMARK", result)
        else:
            result = run_case(name, pdf_bytes, repeats=args.repeats)
            print("BENCHMARK", result)


if __name__ == "__main__":
    main()
