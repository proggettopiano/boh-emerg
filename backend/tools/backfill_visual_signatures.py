"""Backfill visual signatures for existing PDF pages.

This script updates pdf_pages.visual_signature for pages that already have text.
It does not run OCR and does not touch search/ranking behavior.

By default it runs in dry-run mode and reports what it would update.
Use --execute to persist changes.
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))

from pdf_processor import _build_visual_signature  # noqa: E402

MONGO_URL = os.getenv("MONGO_URL")
DB_NAME = os.getenv("DB_NAME", "scorelib")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill visual signatures for existing text-backed PDF pages.")
    parser.add_argument(
        "--pdf-ids",
        help="Comma-separated list of PDF IDs to process. If omitted, all eligible PDFs are processed.",
    )
    parser.add_argument(
        "--pdf-ids-file",
        help="Path to a file containing one PDF ID per line.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Persist the updates instead of dry-run.",
    )
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Recompute visual signatures even when one already exists.",
    )
    parser.add_argument(
        "--limit-pages",
        type=int,
        default=0,
        help="Optional limit on page documents to process overall.",
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
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    ids.append(line)
    return sorted(dict.fromkeys(ids))


def make_mongo_client():
    if not MONGO_URL:
        raise RuntimeError("MONGO_URL is not set")
    if not DB_NAME:
        raise RuntimeError("DB_NAME is not set")
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    return client[DB_NAME]


def gather_page_docs(db, pdf_ids=None, limit_pages=0, recompute=False):
    query = {"text": {"$ne": ""}}
    if pdf_ids:
        query["pdf_id"] = {"$in": pdf_ids}
    if not recompute:
        query["$or"] = [
            {"visual_signature": {"$exists": False}},
            {"visual_signature": None},
            {"visual_signature": {}},
        ]

    projection = {
        "_id": 1,
        "pdf_id": 1,
        "page": 1,
        "text": 1,
        "visual_signature": 1,
    }
    cursor = db.pdf_pages.find(query, projection).sort([("pdf_id", 1), ("page", 1)])
    if limit_pages and limit_pages > 0:
        cursor = cursor.limit(limit_pages)
    return list(cursor)


def gather_pdf_docs(db, pdf_ids):
    query = {"id": {"$in": pdf_ids}} if pdf_ids else {}
    projection = {"id": 1, "file_path": 1, "title": 1}
    cursor = db.pdfs.find(query, projection)
    return {doc["id"]: doc for doc in cursor}


def group_pages_by_pdf(page_docs):
    grouped = defaultdict(list)
    for page_doc in page_docs:
        grouped[page_doc["pdf_id"]].append(page_doc)
    return grouped


def report_summary(page_docs, pdf_docs, pdf_ids, execute, recompute):
    matched_pdf_count = len(pdf_docs)
    page_count = len(page_docs)
    already_sized = sum(1 for page in page_docs if page.get("visual_signature"))
    print("Backfill summary")
    print("---------------")
    if pdf_ids:
        print(f"Requested PDF IDs: {', '.join(pdf_ids)}")
    else:
        print("Requested PDF IDs: <all eligible PDFs>")
    print(f"Matched PDF documents: {matched_pdf_count}")
    print(f"Matched page documents: {page_count}")
    print(f"Pages already carrying visual_signature: {already_sized}")
    print(f"Mode: {'execute' if execute else 'dry-run'}")
    print(f"Recompute existing signatures: {'yes' if recompute else 'no'}")
    print()


def process(db, page_docs, pdf_docs, execute):
    grouped = group_pages_by_pdf(page_docs)
    updates = []
    skipped_missing_file = 0
    skipped_out_of_range = 0
    recomputed = 0

    for pdf_id, pages in grouped.items():
        pdf_doc = pdf_docs.get(pdf_id)
        if not pdf_doc or not pdf_doc.get("file_path"):
            skipped_missing_file += len(pages)
            print(f"[skip] pdf_id={pdf_id} reason=missing_file_path")
            continue

        file_path = Path(pdf_doc["file_path"])
        if not file_path.exists():
            skipped_missing_file += len(pages)
            print(f"[skip] pdf_id={pdf_id} reason=file_not_found path={file_path}")
            continue

        try:
            pdf_bytes = file_path.read_bytes()
        except Exception as exc:
            skipped_missing_file += len(pages)
            print(f"[skip] pdf_id={pdf_id} reason=read_failed error={type(exc).__name__}")
            continue

        import fitz

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            skipped_missing_file += len(pages)
            print(f"[skip] pdf_id={pdf_id} reason=pdf_open_failed error={type(exc).__name__}")
            continue

        try:
            for page_doc in pages:
                page_index = int(page_doc["page"]) - 1
                if page_index < 0 or page_index >= len(doc):
                    skipped_out_of_range += 1
                    print(f"[skip] pdf_id={pdf_id} page={page_doc['page']} reason=page_out_of_range")
                    continue

                page = doc[page_index]
                signature = _build_visual_signature(page, timings=None, page_num=page_index)
                if not signature:
                    print(f"[skip] pdf_id={pdf_id} page={page_doc['page']} reason=signature_failed")
                    continue

                recomputed += 1
                updates.append(
                    UpdateOne(
                        {"_id": page_doc["_id"]},
                        {"$set": {"visual_signature": signature}},
                    )
                )
        finally:
            doc.close()

    if execute and updates:
        result = db.pdf_pages.bulk_write(updates, ordered=False)
        print(f"Applied {result.modified_count} visual_signature updates.")
    elif execute:
        print("Nothing to apply.")

    print()
    print("Backfill details")
    print("---------------")
    print(f"Pages with visual signatures recomputed: {recomputed}")
    print(f"Pages skipped due to missing file/path or load errors: {skipped_missing_file}")
    print(f"Pages skipped because page index was out of range: {skipped_out_of_range}")
    print(f"Planned updates: {len(updates)}")


def main():
    args = parse_args()
    pdf_ids = load_pdf_ids(args)
    db = make_mongo_client()

    page_docs = gather_page_docs(db, pdf_ids=pdf_ids or None, limit_pages=args.limit_pages, recompute=args.recompute)
    if not page_docs:
        print("No eligible page documents found.")
        return

    matched_pdf_ids = sorted({doc["pdf_id"] for doc in page_docs})
    pdf_docs = gather_pdf_docs(db, matched_pdf_ids)
    report_summary(page_docs, pdf_docs, pdf_ids, args.execute, args.recompute)
    process(db, page_docs, pdf_docs, args.execute)


if __name__ == "__main__":
    main()