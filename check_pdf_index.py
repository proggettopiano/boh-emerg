import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Carica .env esplicitamente
load_dotenv(".env")
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

async def check_pdf():
    mongo_url = os.environ.get("MONGO_URL")
    print(f"DEBUG: MONGO_URL = {mongo_url[:50] if mongo_url else 'NOT SET'}...")
    if not mongo_url:
        print("MONGO_URL not set")
        return
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[os.environ.get("DB_NAME", "scorelib")]
    
    # Check the PDF document
    pdf = await db.pdfs.find_one({"_id": "pdf_046c921a6094"})
    if pdf:
        print(f"PDF found:")
        print(f"  _id: {pdf.get('_id')}")
        print(f"  filename: {pdf.get('filename')}")
        print(f"  pages: {len(pdf.get('pages', []))}")
        if pdf.get('pages'):
            page1 = pdf['pages'][0]
            print(f"  Page 1 text length: {len(page1.get('text', ''))}")
            if page1.get('text'):
                print(f"  Page 1 first 200 chars: {page1.get('text')[:200]}")
            else:
                print(f"  Page 1 text: EMPTY - OCR status: used_ocr={page1.get('used_ocr')}")
    else:
        print("PDF not found in database")
    
    client.close()

asyncio.run(check_pdf())
