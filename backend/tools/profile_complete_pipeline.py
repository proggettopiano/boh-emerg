"""Complete pipeline profiling with all phases: open, render, OCR, clean, index, Mongo save, Drive upload."""
import io
import json
import time
from pathlib import Path
from typing import Dict, Any

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_processor

OUT = Path(__file__).resolve().parents[1] / 'bench_outputs'
OUT.mkdir(exist_ok=True)


def make_text_only_pdf_100pages() -> bytes:
    """Native text PDF, 100 pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, 101):
        c.setFont("Helvetica", 12)
        c.drawString(40, 750, f"Native text page {i}")
        c.drawString(40, 730, "Canto 542 - Gesu Cristo e la pace")
        for y, line_offset in enumerate(range(710, 600, -12)):
            c.drawString(40, line_offset, f"Line {y+1} - sample native text for page {i}.")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_image_only_pdf_100pages() -> bytes:
    """Scanned/image-only PDF, 100 pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, 101):
        img = Image.new("RGB", (520, 170), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
        draw.text((20, 20), f"SCANNED IMAGE PAGE {i}", fill="black", font=font)
        draw.text((20, 50), "Canto 542 - Gesu Cristo e la pace", fill="black", font=font)
        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        c.drawInlineImage(Image.open(io.BytesIO(img_buf.getvalue())), 40, 420, width=520, height=170)
        c.showPage()
    c.save()
    return buf.getvalue()


def make_mixed_pdf_100pages() -> bytes:
    """Mixed PDF: native text + images, 100 pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, 101):
        # text part
        c.setFont("Helvetica", 12)
        c.drawString(40, 750, f"Mixed page {i} - native text")
        c.drawString(40, 730, "Canto 542 - Gesu Cristo e la pace")
        # image part
        img = Image.new("RGB", (520, 170), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
        draw.text((20, 20), f"INLINE IMAGE PAGE {i}", fill="black", font=font)
        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        c.drawInlineImage(Image.open(io.BytesIO(img_buf.getvalue())), 40, 420, width=520, height=170)
        c.showPage()
    c.save()
    return buf.getvalue()


def profile_pipeline(name: str, pdf_bytes: bytes) -> Dict[str, Any]:
    """Profile complete pipeline: open, render, OCR, clean, index, Mongo."""
    results = {
        'name': name,
        'phases': {},
    }
    
    # Phase 1: Open PDF
    start = time.perf_counter()
    import fitz
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        doc.close()
    except Exception as e:
        results['phases']['open_pdf_ms'] = (time.perf_counter() - start) * 1000.0
        results['error'] = str(e)
        return results
    phase_open_ms = (time.perf_counter() - start) * 1000.0
    results['phases']['open_pdf_ms'] = phase_open_ms
    
    # Phase 2: Extract pages (includes rendering and OCR)
    start = time.perf_counter()
    timings = {}
    pages_text, raw_texts, total, used_ocr, page_labels = pdf_processor.extract_pages(pdf_bytes, timings=timings)
    phase_extract_ms = (time.perf_counter() - start) * 1000.0
    
    # Break down extract into sub-phases from timings
    page_text_ms = timings.get('page_text_ms', 0)
    page_images_ms = timings.get('page_images_ms', 0)
    rapidocr_ms = timings.get('rapidocr_ms', 0)
    tesseract_ms = timings.get('tesseract_ms', 0)
    ocr_total_ms = rapidocr_ms + tesseract_ms
    
    results['phases']['extract_pages_ms'] = phase_extract_ms
    results['phases']['page_text_extraction_ms'] = page_text_ms
    results['phases']['page_images_detection_ms'] = page_images_ms
    results['phases']['ocr_total_ms'] = ocr_total_ms
    results['phases']['rapidocr_ms'] = rapidocr_ms
    results['pages_processed'] = total
    results['pages_with_ocr'] = timings.get('pages_with_ocr', 0)
    results['pages_native'] = total - timings.get('pages_with_ocr', 0)
    
    # Phase 3: Text cleaning (rough estimate: process all pages)
    start = time.perf_counter()
    for page_text in pages_text:
        pdf_processor.clean_pdf_text(page_text)
    phase_clean_ms = (time.perf_counter() - start) * 1000.0
    results['phases']['text_cleaning_ms'] = phase_clean_ms
    
    # Phase 4: Index preparation (simulate document indexing)
    start = time.perf_counter()
    index_docs = []
    for idx, text in enumerate(pages_text):
        doc = {
            'page': idx + 1,
            'text': text,
            'text_clean': pdf_processor.clean_pdf_text(text),
            'page_label': page_labels[idx] if idx < len(page_labels) else str(idx+1),
        }
        index_docs.append(doc)
    phase_index_prep_ms = (time.perf_counter() - start) * 1000.0
    results['phases']['indexing_preparation_ms'] = phase_index_prep_ms
    
    # Phase 5: MongoDB save (simulated if no connection, or real if available)
    start = time.perf_counter()
    mongo_saved = False
    try:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        mongo_url = os.environ.get("MONGO_URL")
        if mongo_url:
            client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
            db = client['scorelib_profile']
            collection = db[f'pipeline_test_{int(time.time())}']
            docs = [
                UpdateOne(
                    {'page': idx + 1},
                    {'$set': index_docs[idx]},
                    upsert=True,
                )
                for idx in range(len(index_docs))
            ]
            result = collection.bulk_write(docs, ordered=False)
            mongo_saved = True
            collection.drop()
    except Exception as e:
        pass
    phase_mongo_ms = (time.perf_counter() - start) * 1000.0
    results['phases']['mongodb_save_ms'] = phase_mongo_ms if mongo_saved else 0
    results['mongo_saved'] = mongo_saved
    
    # Phase 6: Google Drive upload (simulated: 100ms per 10 pages)
    start = time.perf_counter()
    # Simulated upload time: 50ms base + 10ms per page
    time.sleep(0.05 + len(index_docs) * 0.0001)
    phase_drive_ms = (time.perf_counter() - start) * 1000.0
    results['phases']['google_drive_upload_ms'] = phase_drive_ms
    
    # Calculate percentages
    total_ms = sum(v for k, v in results['phases'].items() if isinstance(v, (int, float)))
    results['total_ms'] = total_ms
    results['avg_per_page_ms'] = total_ms / total if total > 0 else 0
    
    # Convert phases to include percentages
    phases_with_pct = {}
    for phase_name, phase_ms in results['phases'].items():
        if phase_ms > 0:
            pct = (phase_ms / total_ms) * 100 if total_ms > 0 else 0
            phases_with_pct[phase_name] = {
                'ms': round(phase_ms, 2),
                'percent': round(pct, 1),
            }
    results['phases'] = phases_with_pct
    
    return results


def main():
    print("Generating 100-page PDFs...")
    cases = [
        ('digital_100pages', make_text_only_pdf_100pages()),
        ('scanned_100pages', make_image_only_pdf_100pages()),
        ('mixed_100pages', make_mixed_pdf_100pages()),
    ]
    
    all_results = []
    for name, pdf_bytes in cases:
        print(f"\n{'='*70}")
        print(f"Profiling: {name}")
        print(f"{'='*70}")
        
        pdf_path = OUT / f"{name}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        print(f"PDF saved: {pdf_path} ({len(pdf_bytes)/1024/1024:.1f} MB)")
        
        result = profile_pipeline(name, pdf_bytes)
        all_results.append(result)
        
        print(f"\nPhase Breakdown:")
        print(f"  Total time: {result['total_ms']:.0f} ms ({result['total_ms']/1000:.1f}s)")
        print(f"  Pages processed: {result['pages_processed']}")
        print(f"  Pages with OCR: {result['pages_with_ocr']}")
        print(f"  Pages native: {result['pages_native']}")
        print(f"  Avg per page: {result['avg_per_page_ms']:.1f} ms")
        print()
        
        # Sort by time descending
        sorted_phases = sorted(result['phases'].items(), key=lambda x: x[1]['ms'], reverse=True)
        for phase_name, phase_data in sorted_phases:
            ms = phase_data['ms']
            pct = phase_data['percent']
            bar_len = int(pct / 2)
            bar = '█' * bar_len
            print(f"  {phase_name:35s} {ms:10.1f} ms {pct:6.1f}% {bar}")
        
        # Identify bottleneck
        bottleneck = max(sorted_phases, key=lambda x: x[1]['ms'])
        if bottleneck[1]['percent'] >= 70:
            print(f"\n⚠️  BOTTLENECK: {bottleneck[0]} = {bottleneck[1]['percent']:.1f}% of total time")
        else:
            print(f"\n✅ No single phase exceeds 70%; balanced pipeline")
    
    # Save full results
    OUT_JSON = OUT / 'complete_pipeline_profile.json'
    OUT_JSON.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n\nFull results saved: {OUT_JSON}")


if __name__ == '__main__':
    main()
