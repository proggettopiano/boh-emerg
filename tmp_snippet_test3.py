import re, sys, os
from importlib import util
# Load pdf_processor module
spec = util.spec_from_file_location('pdf_processor', os.path.join(os.getcwd(),'backend','pdf_processor.py'))
pdf = util.module_from_spec(spec)
spec.loader.exec_module(pdf)

# replicate new JS logic in Python
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
    text = re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Ý][a-zà-ÿ])", ' ', text)
    text = re.sub(r"\s+", ' ', text)
    return text.strip()


def sanitize_snippet_text_new(value):
    raw = str(value)
    if re.search(r"\r?\n", raw):
        parts = [sanitize_search_text(line).strip() for line in re.split(r"\r?\n", raw)]
        parts = [p for p in parts if p]
        return ', '.join(parts)
    # Only convert when raw contains explicit newlines; otherwise preserve sentence punctuation
    if re.search(r"\r?\n", raw):
        parts = [sanitize_search_text(line).strip() for line in re.split(r"\r?\n", raw)]
        parts = [p for p in parts if p]
        return ', '.join(parts)
    return sanitize_search_text(raw)

# Test cases
cases = [
    ("Multiline original", "Riga uno\nRiga due\nRiga tre"),
    ("Normal sentences", "Questo è un periodo. Questo è un altro periodo."),
    ("Mixed long fragments", "Una frase molto lunga che supera la soglia di lunghezza e dovrebbe rimanere intatta. Un'altra frase molto lunga che sembra una frase normale.")
]

for name, original in cases:
    print('---', name)
    print('Original:', repr(original))
    snippet = pdf.make_snippet(original, 'Riga')
    print('make_snippet:', repr(snippet))
    # debug
    raw = snippet
    dotParts = re.split(r"\.\s+", raw)
    print('dotParts_len=', len(dotParts), 'dotParts=', dotParts)
    # determine branch
    if re.search(r"\r?\n", raw):
        branch = 'newline-split'
    elif len(dotParts) >= 3 and all((p.strip() and len(p.strip()) <= 60) for p in dotParts):
        branch = 'dot-convert'
    else:
        branch = 'sanitize-whole'
    rendered = sanitize_snippet_text_new(snippet)
    print('branch:', branch)
    print('sanitizeSnippetText result:', repr(rendered))
    print('sanitize_search_text(raw):', repr(sanitize_search_text(snippet)))
    print()
