import re

# Read stable file
with open('./tmp/stable_server.py', 'r', encoding='utf-8', errors='ignore') as f:
    stable = f.read()

# Read main file
with open('./backend/server.py', 'r', encoding='utf-8', errors='ignore') as f:
    main = f.read()

# Find upload-related endpoints in stable
stable_upload_matches = re.findall(r'@api\.(?:post|get|put)\(["\']([^"\']*)["\']', stable, re.IGNORECASE)
stable_upload = [m for m in stable_upload_matches if 'upload' in m.lower() or 'drive' in m.lower()]
print("=== STABLE upload/drive endpoints ===")
for m in sorted(set(stable_upload)):
    print(f"  {m}")

# Find upload-related endpoints in main
main_upload_matches = re.findall(r'@api\.(?:post|get|put)\(["\']([^"\']*)["\']', main, re.IGNORECASE)
main_upload = [m for m in main_upload_matches if 'upload' in m.lower() or 'drive' in m.lower()]
print("\n=== MAIN upload/drive endpoints ===")
for m in sorted(set(main_upload)):
    print(f"  {m}")

# Find key functions in stable
print("\n=== KEY FUNCTIONS in STABLE ===")
stable_funcs = re.findall(r'async def ([\w_]+).*?(?:async def|\Z)', stable, re.DOTALL)
print(f"Found {len(stable_funcs)} functions")
stable_upload_funcs = [f for f in stable_funcs if 'upload' in f.lower() or 'drive' in f.lower() or 'share' in f.lower()]
for f in sorted(set(stable_upload_funcs))[:10]:
    print(f"  {f}")

# Find key functions in main
print("\n=== KEY FUNCTIONS in MAIN ===")
main_funcs = re.findall(r'async def ([\w_]+).*?(?:async def|\Z)', main, re.DOTALL)
print(f"Found {len(main_funcs)} functions")
main_upload_funcs = [f for f in main_funcs if 'upload' in f.lower() or 'drive' in f.lower() or 'share' in f.lower()]
for f in sorted(set(main_upload_funcs))[:10]:
    print(f"  {f}")

# Check for specific patterns
print("\n=== PATTERN CHECK ===")
print(f"STABLE has 'create_pdf_upload_url': {'create_pdf_upload_url' in stable}")
print(f"MAIN has 'create_pdf_upload_url': {'create_pdf_upload_url' in main}")
print(f"STABLE has presigned: {'presigned' in stable.lower()}")
print(f"MAIN has presigned: {'presigned' in main.lower()}")
print(f"STABLE has upload_jobs: {'upload_jobs' in stable}")
print(f"MAIN has upload_jobs: {'upload_jobs' in main}")
