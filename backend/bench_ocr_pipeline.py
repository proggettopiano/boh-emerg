"""Profile the PDF ingestion pipeline for native extraction, OCR, and MongoDB save."""
import argparse
import io
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pymongo import MongoClient, UpdateOne
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import pdf_processor

OUTPUT_DIR = Path(__file__).parent / "bench_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
logging.getLogger("rapidocr").setLevel(logging.WARNING)


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


def _get_mongo_client():
    load_dotenv(Path(__file__).parent.parent / ".env")
    mongo_url = os.environ.get("MONGO_URL")
    if not mongo_url:
        return None
    return MongoClient(mongo_url, serverSelectionTimeoutMS=5000)


def _save_pages_to_mongo(pdf_id: str, page_texts, raw_texts, page_labels, timings):
    client = _get_mongo_client()
    if client is None:
        return None

    db_name = os.environ.get("DB_NAME", "scorelib")
    db = client[db_name]
    collection_name = f"bench_ocr_pipeline_{uuid.uuid4().hex[:8]}"
    page_collection = db[collection_name]
    docs = []
    for idx, text in enumerate(page_texts):
        raw = raw_texts[idx] if idx < len(raw_texts) else ""
        docs.append(
            UpdateOne(
                {"pdf_id": pdf_id, "page": idx + 1},
                {
                    "$set": {
                        "pdf_id": pdf_id,
                        "page": idx + 1,
                        "text": text,
                        "text_raw": raw,
                        "text_clean": text,
                        "text_normalized": pdf_processor.normalize_pdf_text(text),
                        "page_label": page_labels[idx],
                    }
                },
                upsert=True,
            )
        )

    start = time.perf_counter()
    result = page_collection.bulk_write(docs, ordered=False)
    save_ms = (time.perf_counter() - start) * 1000.0
    timings["mongo_save_ms"] = save_ms
    timings["mongo_matched_count"] = result.matched_count
    timings["mongo_modified_count"] = result.modified_count
    timings["mongo_upserted_count"] = len(result.upserted_ids)
    page_collection.drop()
    return timings


def run_case(name: str, pdf_bytes: bytes, repeats: int = 1, save_to_mongo: bool = False):
    out_path = OUTPUT_DIR / f"{name}.pdf"
    out_path.write_bytes(pdf_bytes)

    def measure_once():
        timings = {}
        start = time.perf_counter()
        pages, raw_texts, total, used_ocr, labels = pdf_processor.extract_pages(pdf_bytes, timings=timings)
        timings["total_extraction_ms"] = (time.perf_counter() - start) * 1000.0
        timings["total_pages"] = total
        timings["used_ocr"] = used_ocr
        timings["rapid_words"] = len([w for p in pages for w in p.split() if len(w) > 2])
        timings["page_label_sample"] = labels[:3]
        if save_to_mongo:
            _save_pages_to_mongo(f"benchmark_{name}_{uuid.uuid4().hex[:8]}", pages, raw_texts, labels, timings)
        return timings

    measurements = [measure_once()]
    warm_measurements = [measure_once() for _ in range(repeats - 1)] if repeats > 1 else []
    all_measurements = measurements + warm_measurements

    summary = {
        "name": name,
        "pages": all_measurements[0]["total_pages"] if all_measurements else 0,
        "repeat_count": len(all_measurements),
        "avg_total_extraction_ms": sum(m["total_extraction_ms"] for m in all_measurements) / len(all_measurements),
        "first_run": measurements[0],
        "warm_runs": warm_measurements,
    }
    return summary


def print_summary(summary):
    print("\nBENCHMARK SUMMARY")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Profile PDF ingestion pipeline: native text, OCR, and MongoDB save.")
    parser.add_argument("--case", choices=["image_only_pdf", "long_54_pages_pdf"], default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--mongo", action="store_true", help="Measure MongoDB save time if MONGO_URL is configured.")
    args = parser.parse_args()

    cases = [
        ("image_only_pdf", make_image_only_pdf()),
        ("long_54_pages_pdf", make_long_pdf(54)),
    ]
    if args.case:
        cases = [next(item for item in cases if item[0] == args.case)]

    for name, pdf_bytes in cases:
        print(f"Running case: {name}")
        result = run_case(name, pdf_bytes, repeats=args.repeats, save_to_mongo=args.mongo)
        print_summary(result)


if __name__ == "__main__":
    main()
