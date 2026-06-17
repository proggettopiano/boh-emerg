import json
from pathlib import Path
import fitz

BASE = Path(__file__).resolve().parents[1]
PDF_PATH = BASE / 'bench_outputs' / 'long_54_pages_pdf.pdf'
OUT_PATH = BASE / 'bench_outputs' / 'diagnostics_54.json'

if not PDF_PATH.exists():
    print('PDF not found:', PDF_PATH)
    raise SystemExit(1)

doc = fitz.open(PDF_PATH)
results = []
for pno in range(doc.page_count):
    page = doc.load_page(pno)
    text = page.get_text('text')
    text_len = len(text or '')
    text_preview = (text or '')[:300]
    d = page.get_text('dict')
    blocks = d.get('blocks', []) if isinstance(d, dict) else []
    blocks_count = len(blocks)
    text_blocks = sum(1 for b in blocks if b.get('type') == 0)
    image_blocks = sum(1 for b in blocks if b.get('type') == 1)
    # page.get_images() returns list of images (xref, smth...)
    try:
        images = page.get_images(full=True)
    except Exception:
        images = []
    images_count = len(images)
    # fonts from spans
    fonts = set()
    for b in blocks:
        if b.get('type') != 0:
            continue
        for line in b.get('lines', []):
            for span in line.get('spans', []):
                f = span.get('font')
                if f:
                    fonts.add(f)
    fonts_list = sorted(list(fonts))
    has_text_layer = text_len > 0 or text_blocks > 0
    # build a JSON-serializable summary of the get_text('dict') content
    dict_summary = {
        'blocks_count': blocks_count,
        'text_blocks': text_blocks,
        'image_blocks': image_blocks,
    }

    results.append({
        'page': pno + 1,
        'text_len': text_len,
        'text_preview': text_preview,
        'blocks_count': blocks_count,
        'text_blocks': text_blocks,
        'image_blocks': image_blocks,
        'images_count': images_count,
        'fonts': fonts_list,
        'has_text_layer': has_text_layer,
        'get_text_dict_summary': dict_summary,
    })

OUT_PATH.write_text(json.dumps({'pages': results}, ensure_ascii=False, indent=2))
print('WROTE', OUT_PATH)
