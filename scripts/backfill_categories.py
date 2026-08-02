"""Backfill the 分类 (category) relation for books already in Notion.

The original weread2notion-pro project relied on the (now defunct) private
WeRead API, which returned a ``categories`` array on ``/book/info``. The
official Agent API Gateway instead returns a single ``category`` string
like ``"精品小说-社会小说"``. book.py was updated to handle both shapes
(see commit 5d82f64), but the 256 books that were already imported before
that fix has no category relation set.

This script walks every book page in the Notion 书架 database, fetches
the live ``/book/info`` for each, and patches the page's 分类 relation.

Run once after pulling the new book.py; safe to re-run (idempotent —
get_relation_id is cached and reuses existing 分类 pages).
"""
import os
import sys
import time

from dotenv import load_dotenv

# Make repo importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))

from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.weread_api import WeReadApi
from weread2notionpro.utils import get_property_value


def parse_category(raw):
    """Split a string like '精品小说-社会小说' into ['精品小说', '社会小说']."""
    if not raw:
        return []
    raw = str(raw)
    for sep in ("-", "·", "/", ">"):
        if sep in raw:
            return [s.strip() for s in raw.split(sep) if s.strip()]
    s = raw.strip()
    return [s] if s else []


def main():
    nh = NotionHelper()
    api = WeReadApi()

    print("Fetching all books from Notion 书架 database...")
    books = nh.get_all_book()
    print(f"  found {len(books)} books")

    updated = 0
    skipped = 0
    failed = 0
    for i, (book_id, info) in enumerate(books.items(), 1):
        page_id = info.get("pageId")
        existing = info.get("category") or []
        if existing:
            skipped += 1
            if i % 50 == 0:
                print(f"  [{i}/{len(books)}] {book_id}: already has category, skip")
            continue

        # Throttle a bit so the gateway doesn't rate-limit us on 256 calls
        if i % 10 == 0:
            time.sleep(0.5)

        try:
            bookinfo = api.get_bookinfo(book_id)
        except Exception as e:
            print(f"  [{i}/{len(books)}] {book_id}: get_bookinfo failed: {e}")
            failed += 1
            continue

        cat_items = parse_category(bookinfo.get("category"))
        if not cat_items:
            skipped += 1
            continue

        # Resolve each category name to a Notion page (auto-create if missing)
        relation_ids = []
        for name in cat_items:
            try:
                rid = nh.get_relation_id(
                    name,
                    nh.category_database_id,
                    "https://www.notion.so/icons/tag_gray.svg",
                )
                if rid:
                    relation_ids.append(rid)
            except Exception as e:
                print(f"  [{i}/{len(books)}] {book_id}: get_relation_id({name}) failed: {e}")
                continue

        if not relation_ids:
            skipped += 1
            continue

        # Patch the page
        try:
            nh.client.pages.update(
                page_id=page_id,
                properties={"分类": {"relation": [{"id": rid} for rid in relation_ids]}},
            )
            updated += 1
            if i % 10 == 0 or updated <= 5:
                print(
                    f"  [{i}/{len(books)}] {book_id}: "
                    f"set 分类 = {cat_items}"
                )
        except Exception as e:
            print(f"  [{i}/{len(books)}] {book_id}: pages.update failed: {e}")
            failed += 1

    print()
    print(f"done: {updated} updated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
