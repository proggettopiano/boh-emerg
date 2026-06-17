import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_processor

PDF_PATH = Path(__file__).resolve().parents[1] / 'bench_outputs' / 'long_54_pages_pdf.pdf'
if not PDF_PATH.exists():
    print(json.dumps({"error": "PDF not found", "path": str(PDF_PATH)}))
    raise SystemExit(1)

pdf_bytes = PDF_PATH.read_bytes()

# collect timings and page_details
timings = {}
pages_text, raw_texts, total, used_ocr, page_labels = pdf_processor.extract_pages(pdf_bytes, timings=timings)

page_details = timings.get('page_details', [])

results = []
for i, info in enumerate(page_details):
    # Build reason string
    reason = ",".join(info.get('reason', [])) if info.get('reason') else ''
    results.append({
        'page': info.get('page', i+1),
        'raw_length': info.get('raw_length', 0),
        'word_count': info.get('word_count', 0),
        'reason': reason,
        'ocr_attempted': bool(info.get('ocr_attempted')),
        'ocr_used': bool(info.get('ocr_used')),
        'page_images': bool(info.get('page_images')),
        'is_noisy': bool(info.get('is_noisy')),
        'page_label': page_labels[i] if i < len(page_labels) else str(i+1),
    })

# summary counts
native_valid = sum(1 for p in results if not p['ocr_attempted'])
image_pages = sum(1 for p in results if p['page_images'])
noisy_trigger = sum(1 for p in results if 'noisy_text' in (p['reason'] or '').split(','))

out = {'per_page': results, 'summary': {'total_pages': total, 'native_valid_pages': native_valid, 'image_pages': image_pages, 'noisy_trigger_pages': noisy_trigger}}
print(json.dumps(out, ensure_ascii=False))
