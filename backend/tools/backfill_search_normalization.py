"""Selective backfill for PDF search normalization.

This script updates only the selected PDF documents and their pages.
It writes:
- pdfs.title_normalized
- pdf_pages.text_normalized

By default it runs in dry-run mode and reports the changes it would make.
Use --execute only when you want to persist updates.
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_processor import normalize_pdf_text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# SECURITY: Do NOT hardcode credentials in repository.
# Read Mongo connection details from environment variables.
MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "scorelib")


def normalize_pdf_title(title: str) -> str:
    return normalize_pdf_text(title or "")


def normalize_page_text(text: str) -> str:
    return normalize_pdf_text(text or "")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Selective backfill of title_normalized and text_normalized for selected PDFs.")
    parser.add_argument(
        "--pdf-ids",
        help="Comma-separated list of PDF IDs to process.",
    )
    parser.add_argument(
        "--pdf-ids-file",
        help="Path to a file containing one PDF ID per line.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the updates instead of dry-run.",
    )
    parser.add_argument(
        "--limit-pages",
        type=int,
        default=0,
        help="Optional limit for page documents to process per PDF (dry-run only if unspecified).",
    )
    return parser.parse_args()


def load_pdf_ids(args):
    ids = []
    if args.pdf_ids:
        ids.extend([pid.strip() for pid in args.pdf_ids.split(",") if pid.strip()])
    if args.pdf_ids_file:
        path = Path(args.pdf_ids_file)
        if not path.exists():
            raise FileNotFoundError(f"PDF IDs file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ids.append(line)
    if not ids:
        raise ValueError("At least one PDF ID must be provided via --pdf-ids or --pdf-ids-file.")
    return sorted(dict.fromkeys(ids))


def make_mongo_client():
    if not MONGO_URL:
        raise RuntimeError("MONGO_URL is not set")
    if not DB_NAME:
        raise RuntimeError("DB_NAME is not set")
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    return db


def gather_pdf_docs(db, pdf_ids):
    cursor = db.pdfs.find({"id": {"$in": pdf_ids}}, {"id": 1, "title": 1, "title_normalized": 1})
    return {doc["id"]: doc for doc in cursor}


def gather_page_docs(db, pdf_ids, limit_pages=0):
    query = {"pdf_id": {"$in": pdf_ids}}
    projection = {"pdf_id": 1, "page": 1, "text": 1, "text_normalized": 1}
    cursor = db.pdf_pages.find(query, projection)
    if limit_pages and limit_pages > 0:
        cursor = cursor.limit(limit_pages)
    return list(cursor)


def build_updates(pdf_docs, page_docs):
    pdf_updates = []
    page_updates = []

    for pdf_id, doc in pdf_docs.items():
        expected = normalize_pdf_title(doc.get("title", ""))
        current = doc.get("title_normalized")
        if current != expected:
            pdf_updates.append((pdf_id, expected, current))

    for page in page_docs:
        expected = normalize_page_text(page.get("text", ""))
        current = page.get("text_normalized")
        if current != expected:
            page_updates.append((page["_id"], page["pdf_id"], page.get("page"), expected, current))

    return pdf_updates, page_updates


def report_changes(pdf_updates, page_updates, pdf_docs_count, page_docs_count, pdf_ids):
    print("Backfill summary")
    print("---------------")
    print(f"Requested PDF IDs: {', '.join(pdf_ids)}")
    print(f"Matched PDF documents: {pdf_docs_count}")
    print(f"Matched page documents: {page_docs_count}")
    print(f"PDFs needing title_normalized update: {len(pdf_updates)}")
    print(f"Pages needing text_normalized update: {len(page_updates)}")
    print()
    if pdf_updates:
        print("PDFs to update title_normalized:")
        for pdf_id, expected, current in pdf_updates:
            print(f"  - {pdf_id}: current={'<missing>' if current is None else 'present'}")
    if page_updates:
        update_sample = page_updates[:10]
        print("\nSample page updates (up to 10):")
        for page_id, pdf_id, page_num, expected, current in update_sample:
            print(f"  - pdf_id={pdf_id} page={page_num} current={'<missing>' if current is None else 'present'}")
    if not pdf_updates and not page_updates:
        print("No updates required: all selected documents are already normalized.")


def apply_updates(db, pdf_updates, page_updates):
    pdf_ops = [UpdateOne({"id": pdf_id}, {"$set": {"title_normalized": expected}}) for pdf_id, expected, _ in pdf_updates]
    page_ops = [UpdateOne({"_id": page_id}, {"$set": {"text_normalized": expected}}) for page_id, _, _, expected, _ in page_updates]

    if pdf_ops:
        result = db.pdfs.bulk_write(pdf_ops, ordered=False)
        print(f"Applied {result.modified_count} PDF title_normalized updates.")
    if page_ops:
        result = db.pdf_pages.bulk_write(page_ops, ordered=False)
        print(f"Applied {result.modified_count} page text_normalized updates.")


def main():
    args = parse_args()
    pdf_ids = load_pdf_ids(args)
    db = make_mongo_client()

    pdf_docs = gather_pdf_docs(db, pdf_ids)
    if not pdf_docs:
        raise RuntimeError("No matching PDF documents found for the requested IDs.")

    page_docs = gather_page_docs(db, list(pdf_docs.keys()), limit_pages=args.limit_pages)
    pdf_updates, page_updates = build_updates(pdf_docs, page_docs)

    report_changes(pdf_updates, page_updates, len(pdf_docs), len(page_docs), pdf_ids)

    if args.execute:
        if not pdf_updates and not page_updates:
            print("Nothing to apply.")
            return
        print("Applying updates...")
        apply_updates(db, pdf_updates, page_updates)
    else:
        print("Dry-run mode: no updates written. Use --execute to apply changes.")


if __name__ == "__main__":
    main()
