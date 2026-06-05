import os, re
root = 'frontend'
patterns = [
    ('loading', re.compile(r'\bloading\b', re.I)),
    ('caricamento', re.compile(r'caricamento', re.I)),
    ('AuthContext', re.compile(r'AuthContext')),
    ('useAuth', re.compile(r'useAuth')),
    ('UploadModal', re.compile(r'UploadModal')),
    ('Trascina', re.compile(r'Trascina')),
    ('OCR automatico', re.compile(r'OCR automatico')),
    ('/api/pdfs/upload', re.compile(r'/api/pdfs/upload')),
]
for dp, _, fs in os.walk(root):
    for f in fs:
        if f.endswith(('.js', '.jsx', '.ts', '.tsx')):
            p = os.path.join(dp, f)
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
            except Exception:
                continue
            for name, pat in patterns:
                if pat.search(txt):
                    print(name, p)
