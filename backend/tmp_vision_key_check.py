import os
import base64
import io
import json

import httpx
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter


def make_test_pdf_text(text: str) -> bytes:
    img = Image.new('RGB', (600, 200), color='white')
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('arial.ttf', 40)
    except Exception:
        font = ImageFont.load_default()
    d.text((10, 60), text, fill='black', font=font)

    buf_img = io.BytesIO()
    img.save(buf_img, format='PNG')
    buf_img.seek(0)

    buf_pdf = io.BytesIO()
    c = canvas.Canvas(buf_pdf, pagesize=letter)
    c.drawInlineImage(Image.open(buf_img), 100, 400, width=400, height=150)
    c.showPage()
    c.save()
    return buf_pdf.getvalue()


def check_api_key(api_key: str) -> None:
    pdf_bytes = make_test_pdf_text("TEST OCR TEXT")
    try:
        import fitz
    except ImportError:
        raise SystemExit("Please install PyMuPDF in your environment.")

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    page = doc[0]
    pix = page.get_pixmap(alpha=False, dpi=300)
    img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    image_content = base64.b64encode(buf.getvalue()).decode('utf-8')

    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    payload = {
        "requests": [
            {
                "image": {"content": image_content},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
            }
        ]
    }
    headers = {"Content-Type": "application/json"}

    resp = httpx.post(url, json=payload, headers=headers, timeout=60.0)
    print("URL:", url)
    print("Status code:", resp.status_code)
    print("Response text:", resp.text)
    try:
        data = resp.json()
        print("JSON:", json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == '__main__':
    key = os.environ.get('GOOGLE_VISION_API_KEY') or os.environ.get('GOOGLE_CLOUD_VISION_API_KEY') or os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not key:
        raise SystemExit('Set GOOGLE_VISION_API_KEY or GOOGLE_CLOUD_VISION_API_KEY or GOOGLE_APPLICATION_CREDENTIALS to an API key for a quick check.')
    check_api_key(key)
