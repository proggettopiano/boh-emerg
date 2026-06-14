from pdf_processor import clean_pdf_text
print('CLEAN1=', clean_pdf_text("L’amore è qui"))
print('CLEAN2=', clean_pdf_text("Gesù disse: \"com'è\""))
print('CLEAN3=', clean_pdf_text("Canto ~ 542 ~ della domenica"))
try:
    import pytesseract
    print('PYTESSERACT=OK')
except Exception as e:
    print('PYTESSERACT-ERROR', type(e).__name__, e)
