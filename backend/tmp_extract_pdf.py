from pdf_processor import extract_pages, clean_pdf_text
from io import BytesIO
from reportlab.pdfgen import canvas
import pytesseract

print('TESSERACT VERSION:', pytesseract.get_tesseract_version())

# PDF with enough text, should not trigger OCR
buf = BytesIO()
c = canvas.Canvas(buf)
c.drawString(100, 750, "L’amore è qui")
c.drawString(100, 730, "Canto ~ 542 ~ della domenica")
c.showPage()
c.save()
pdf_bytes = buf.getvalue()
pages_text, total, used_ocr, labels = extract_pages(pdf_bytes)
print('PAGES_TEXT=', pages_text)
print('TOTAL=', total, 'USED_OCR=', used_ocr, 'LABELS=', labels)

# PDF with tiny text to force OCR fallback if possible
buf2 = BytesIO()
c2 = canvas.Canvas(buf2)
c2.drawString(100, 750, "X")
c2.showPage()
c2.save()
pdf_bytes2 = buf2.getvalue()
pages_text2, total2, used_ocr2, labels2 = extract_pages(pdf_bytes2)
print('PAGES2_TEXT=', pages_text2)
print('TOTAL2=', total2, 'USED_OCR2=', used_ocr2, 'LABELS2=', labels2)
