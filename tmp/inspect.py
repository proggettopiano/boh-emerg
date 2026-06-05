from pathlib import Path
text = Path('tmp/branch_server.py').read_text(encoding='utf-16')
lines = text.splitlines()
terms = [
    '@api.post("/libraries")',
    '@api.get("/libraries")',
    '@api.get("/libraries/hidden")',
    '@api.get("/libraries/{lib_id}")',
    '@api.post("/libraries/{lib_id}/pdfs")',
    '@api.delete("/libraries/{lib_id}/pdfs/{pdf_id}")',
    '@api.delete("/libraries/{lib_id}")',
    '@api.post("/libraries/{lib_id}/hide")',
    '@api.delete("/libraries/{lib_id}/hide")',
    '@api.get("/shared/{share_token}")',
    'async def _user_can_access_pdf',
    '@api.get("/search")'
]
for term in terms:
    print('\n---', term, '---')
    for i, line in enumerate(lines, start=1):
        if term in line:
            print('line', i)
            break
