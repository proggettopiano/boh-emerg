#!/usr/bin/env python3
"""
MongoDB Audit Script

Verify how text is normalized and tokenized in MongoDB
"""
import os
from pymongo import MongoClient
from pdf_processor import normalize_search_query, _tokenize_text

# Connect to MongoDB
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/boh")
client = MongoClient(MONGO_URI)
db = client["boh"]

def audit_mongo():
    """Audit PDF pages collection for normalization"""
    print("=" * 80)
    print("MONGODB AUDIT: TEXT NORMALIZATION AND TOKENIZATION")
    print("=" * 80)
    
    # Query for the specific text pattern
    pattern = {"text": {"$regex": "quando.*saliro", "$options": "i"}}
    
    print(f"\nQuerying: db.pdf_pages.find({pattern})")
    print()
    
    results = list(db.pdf_pages.find(pattern).limit(5))
    
    if not results:
        print("[WARNING] No documents found matching pattern 'quando.*saliro'")
        print("\nTrying broader search...")
        results = list(db.pdf_pages.find({"text": {"$regex": "quando", "$options": "i"}}).limit(3))
        if not results:
            print("[ERROR] No documents with 'quando' found in database")
            return
    
    for i, doc in enumerate(results, 1):
        print(f"\n{'-' * 80}")
        print(f"DOCUMENT {i}")
        print(f"{'-' * 80}")
        
        text = doc.get("text", "")
        print(f"Original text (first 100 chars):")
        print(f"  {text[:100]}...")
        
        # Normalize using same function as search
        normalized = normalize_search_query(text)
        print(f"\nNormalized text (first 100 chars):")
        print(f"  {normalized[:100]}...")
        
        # Tokenize
        tokens = _tokenize_text(normalized)
        print(f"\nTokens (first 20):")
        print(f"  {tokens[:20]}")
        
        # Check for specific transformations
        print(f"\nNormalization checks:")
        
        # Check if accented words are normalized
        if "salirò" in text.lower():
            print(f"  [CHECK] 'salirò' -> in original: YES")
            if "saliro" in normalized.lower():
                print(f"  [OK]    'salirò' normalized to 'saliro': YES")
            else:
                print(f"  [FAIL]  'salirò' NOT normalized correctly: {normalized}")
        
        # Check apostrophes
        has_apostrophe = any(c in text for c in ["'", "'", "`", "´"])
        if has_apostrophe:
            print(f"  [CHECK] Contains apostrophes: YES")
            normalized_apostrophes = any(c in normalized for c in ["'", "'", "`", "´"])
            if not normalized_apostrophes:
                print(f"  [OK]    Apostrophes removed: YES")
            else:
                print(f"  [WARN]  Apostrophes still present: {normalized}")
        
        # Check case
        if text != normalized and text.lower() == normalized.lower():
            print(f"  [OK]    Lowercase normalization: YES")
        
        print(f"\nDocument ID: {doc.get('_id')}")
        print(f"PDF: {doc.get('pdf_name', 'N/A')}")
        print(f"Page: {doc.get('page_number', 'N/A')}")

if __name__ == "__main__":
    try:
        audit_mongo()
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
