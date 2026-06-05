from pathlib import Path
import sys
text = Path('tmp/branch_server.py').read_text(encoding='utf-16')
lines = text.splitlines()
for arg in sys.argv[1:]:
    start,end = map(int,arg.split(':'))
    print(f'--- lines {start}-{end} ---')
    for i in range(start-1, min(end, len(lines))):
        print(f'{i+1}: {lines[i]}')
