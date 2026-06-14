from pdf_processor import extract_pages
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PIL import Image, ImageDraw, ImageFont

img = Image.new('RGB', (600, 200), color='white')
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype('arial.ttf', 40)
except Exception:
    font = ImageFont.load_default()
d.text((10, 60), 'TEST OCR TEXT', fill='black', font=font)

buf_img = BytesIO()
img.save(buf_img, format='PNG')
img_bytes = buf_img.getvalue()

buf_pdf = BytesIO()
c = canvas.Canvas(buf_pdf, pagesize=letter)
c.drawInlineImage(Image.open(BytesIO(img_bytes)), 100, 400, width=400, height=150)
c.showPage()
c.save()
pdf_bytes = buf_pdf.getvalue()

try:
    import pytesseract
    print('pytesseract import OK')
    try:
        print('Tesseract version:', pytesseract.get_tesseract_version())
    except Exception as e:
        print('Tesseract version check failed:', repr(e))
except Exception as e:
    print('pytesseract import failed:', repr(e))

pages_text, total, used_ocr, labels = extract_pages(pdf_bytes)
print('TOTAL PAGES:', total)
print('USED_OCR:', used_ocr)
print('PAGE TEXT:', repr(pages_text[0]))
print('LABELS:', labels)
