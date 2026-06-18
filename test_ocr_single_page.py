#!/usr/bin/env python
import os
import sys
import logging
import glob

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(name)s - %(message)s'
)

# Test single page PDF
from backend.pdf_processor import extract_pages

test_pdf = None

# Try to find a test PDF
pdfs = glob.glob('uploads/**/*.pdf', recursive=True)
if pdfs:
    test_pdf = pdfs[0]

if not test_pdf:
    print('No PDFs found for test')
    sys.exit(1)

print(f'Testing with: {test_pdf}')
print('=' * 70)

# Read PDF file as bytes
with open(test_pdf, 'rb') as f:
    pdf_bytes = f.read()

result = extract_pages(pdf_bytes)

print('=' * 70)
print(f'Result type: {type(result)}')
if isinstance(result, tuple):
    pages_text = result[0]
    print(f'Extracted pages: {len(pages_text)}')
    if pages_text:
        print(f'First page text length: {len(pages_text[0])} chars')
        print(f'First 100 chars: {pages_text[0][:100]}...')
else:
    print(f'Unexpected result format: {result}')
