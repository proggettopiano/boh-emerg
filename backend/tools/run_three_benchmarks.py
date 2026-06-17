import io
import time
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image, ImageDraw, ImageFont
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_processor

OUT = Path(__file__).resolve().parents[1] / 'bench_outputs'
OUT.mkdir(exist_ok=True)


def make_text_only_pdf(pages=3):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, pages + 1):
        c.setFont("Helvetica", 12)
        c.drawString(40, 750, f"This is a native text page {i}")
        c.drawString(40, 730, "Canto 542 - Gesu Cristo e la pace")
        for y, line in enumerate(range(710, 600, -12)):
            c.drawString(40, line, f"Line {y+1} - sample text for page {i}.")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_image_only_pdf(pages=3):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, pages + 1):
        img = Image.new("RGB", (520, 170), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        draw.text((20, 20), f"OCR IMAGE PAGE {i}", fill="black", font=font)
        draw.text((20, 50), "Canto 542 - Gesu Cristo e la pace", fill="black", font=font)
        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        c.drawInlineImage(Image.open(io.BytesIO(img_buf.getvalue())), 40, 420, width=520, height=170)
        c.showPage()
    c.save()
    return buf.getvalue()


def make_mixed_pdf(pages=3):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(1, pages + 1):
        # text
        c.setFont("Helvetica", 12)
        c.drawString(40, 750, f"Mixed page {i} - native text above")
        c.drawString(40, 730, "Canto 542 - Gesu Cristo e la pace")
        # image
        img = Image.new("RGB", (520, 170), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
        except Exception:
            font = ImageFont.load_default()
        draw.text((20, 20), f"INLINE IMAGE {i}", fill="black", font=font)
        img_buf = io.BytesIO()
        img.save(img_buf, format="PNG")
        c.drawInlineImage(Image.open(io.BytesIO(img_buf.getvalue())), 40, 420, width=520, height=170)
        c.showPage()
    c.save()
    return buf.getvalue()


def analyze_pdf_bytes(name, pdf_bytes):
    timings = {}
    start = time.perf_counter()
    pages, raw_texts, total, used_ocr, labels = pdf_processor.extract_pages(pdf_bytes, timings=timings)
    timings['total_extraction_ms'] = (time.perf_counter() - start) * 1000.0
    # compute pages with ocr from timings page_details
    page_details = timings.get('page_details', [])
    pages_with_ocr = sum(1 for p in page_details if p.get('ocr_attempted'))
    pages_native = total - pages_with_ocr
    return {
        'name': name,
        'total_pages': total,
        'pages_ocr': pages_with_ocr,
        'pages_native': pages_native,
        'total_ms': timings['total_extraction_ms'],
        'ocr_avoided': pages_native,
        'timings': timings,
    }


def main():
    cases = [
        ('digital_text_pdf', make_text_only_pdf(3)),
        ('scanned_image_pdf', make_image_only_pdf(3)),
        ('mixed_pdf', make_mixed_pdf(3)),
    ]
    results = []
    for name, pdf in cases:
        Path(OUT / f"{name}.pdf").write_bytes(pdf)
        print(f"Running {name} ...")
        res = analyze_pdf_bytes(name, pdf)
        results.append(res)
        print(json.dumps({k: res[k] for k in ['name','total_pages','pages_ocr','pages_native','total_ms','ocr_avoided']}, indent=2))
    Path(OUT / 'three_benchmarks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
