import os, re
root = '.'
exclude_dirs = {'node_modules', 'build', '.git', '.venv', '__pycache__'}
patterns = [
    ('loading', re.compile(r'\bloading\b', re.I)),
    ('caricamento', re.compile(r'caricamento', re.I)),
    ('AuthContext', re.compile(r'AuthContext')),
    ('useAuth', re.compile(r'useAuth')),
    ('UploadModal', re.compile(r'UploadModal')),
    ('Trascina', re.compile(r'Trascina')),
    ('OCR automatico', re.compile(r'OCR automatico')),
    ('/api/pdfs/upload', re.compile(r'/api/pdfs/upload')),
    ('MAX_UPLOAD_SIZE_BYTES', re.compile(r'MAX_UPLOAD_SIZE_BYTES')),
    ('compress_pdf', re.compile(r'compress_pdf')),
    ('UploadFile = File', re.compile(r'UploadFile\s*=\s*File\(')),
]
for dp, dirs, fs in os.walk(root):
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for f in fs:
        if f.endswith(('.js', '.jsx', '.ts', '.tsx', '.py')):
            p = os.path.join(dp, f)
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as fh:
                    txt = fh.read()
            except Exception:
                continue
            for name, pat in patterns:
                if pat.search(txt):
                    print(name, p)
