import re, sys, os
from importlib import util
# Load pdf_processor module from backend/pdf_processor.py
spec = util.spec_from_file_location('pdf_processor', os.path.join(os.getcwd(),'backend','pdf_processor.py'))
pdf = util.module_from_spec(spec)
spec.loader.exec_module(pdf)

# Sample original page text with newlines
original = "Frase uno\nFrase due\nFrase tre"
query = "Frase"

# Show original
print('ORIGINAL_TEXT:')
print(repr(original))
print()

# Run make_snippet
snippet = pdf.make_snippet(original, query, length=200)
print('MAKE_SNIPPET OUTPUT:')
print(repr(snippet))
print()

# Simulate API response: snippet field equals make_snippet of text_normalized or text
api_snippet = snippet
print('API RESPONSE snippet field:')
print(repr(api_snippet))
print()

# Now replicate the NEW sanitizeSnippetText logic in Python
APOSTROPHE_RE = re.compile(r"[’‘`]")
DECORATIVE_NUMBER_RE = re.compile(r"~\s*\d+\s*~")
NOTE_CHORD_RE = re.compile(r"\b(?:DO|RE|MI|FA|SOL|LA|SI)(?:[#b]|[-/][A-Z0-9#b]+|\d+|maj|min|m|dim|aug|sus|add|7|9|11|13)*\b", re.IGNORECASE)
CHORD_RE = re.compile(r"(?<![A-Za-zÀ-ÿ])(?:[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add)?\d*(?:/[A-G](?:#|b)?\d*)?)(?![A-Za-zÀ-ÿ])", re.IGNORECASE)


def sanitize_search_text(value):
    if value is None:
        return ""
    text = str(value)
    text = APOSTROPHE_RE.sub("'", text)
    text = text.replace('\u00a0', ' ')
    text = text.replace('\r', ' ')
    text = text.replace('\n', ' ')
    text = DECORATIVE_NUMBER_RE.sub(' ', text)
    text = re.sub(r"[œŒ˙…]+", ' ', text)
    text = NOTE_CHORD_RE.sub(' ', text)
    text = CHORD_RE.sub(' ', text)
    text = re.sub(r"(?<=[A-Za-zÀ-ÿ])\s*[-–—]\s*(?=[A-Za-zÀ-ÿ])", '', text)
    text = re.sub(r"[^A-Za-z0-9À-ÿ\s'.]+", ' ', text)
    # split camel case glue
    text = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý][a-zà-ÿ])", ' ', text)
    text = re.sub(r"\s+", ' ', text)
    return text.strip()


def sanitize_snippet_text_new(value):
    raw = str(value)
    if re.search(r"\r?\n", raw):
        parts = [sanitize_search_text(line).strip() for line in re.split(r"\r?\n", raw)]
        parts = [p for p in parts if p]
        return ', '.join(parts)
    if (raw.count('. ') or 0) >= 2:
        return re.sub(r"\.\s+", ', ', sanitize_search_text(raw)).strip()
    return sanitize_search_text(raw)

sanitized_new = sanitize_snippet_text_new(api_snippet)
print('AFTER NEW sanitizeSnippetText:')
print(repr(sanitized_new))
