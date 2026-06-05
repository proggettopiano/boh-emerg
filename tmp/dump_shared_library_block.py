from pathlib import Path
text = Path('tmp/branch_server.py').read_text(encoding='utf-16')
lines = text.splitlines()
start = None
end = None
for i, line in enumerate(lines):
    if '# ----------------- Shared Libraries' in line:
        start = i
    if start is not None and '# ----------------- Admin Logs' in line:
        end = i
        break
if start is None:
    raise SystemExit('start not found')
print('\n'.join(lines[start:end]))
