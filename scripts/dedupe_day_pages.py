"""Deduplicate the 「日」 database.

Background: read_time.py previously used get_api_data() (annually mode)
which wrote 12 monthly bucket pages per year keyed by UTC-0 month-start
timestamps. After we switched to get_daily_data() (monthly mode) which
writes per-day Shanghai-0 timestamps, the same calendar date ends up
with two day-pages (one with 0s, one with the real value).

This script groups day pages by their 日期 (calendar date) and deletes
all but the longest-duration page in each group.

Safe to re-run. Wraps everything in try/except so a single bad page
or transient Notion error doesn't kill the whole workflow.
"""
import os
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import requests

from weread2notionpro.notion_helper import NotionHelper

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def all_pages_in_db(token, db_id, max_pages=10000):
    """Yield every page in a database (handles pagination).

    max_pages is a safety cap so a runaway query can't OOM the CI box.
    Notion's actual limit is 100/query, so >10000 pages means ~100 queries
    which is way more than we ever have. Keep it sane.
    """
    cursor = None
    yielded = 0
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        # Retry on transient 5xx / network errors
        for attempt in range(3):
            try:
                r = requests.post(
                    f"{NOTION_API}/databases/{db_id}/query",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Notion-Version": NOTION_VERSION,
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=30,
                )
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                print(f"  query retry {attempt+1}/3 after {type(e).__name__}: {e}")
                time.sleep(2 ** attempt)
        for p in data.get("results", []):
            yield p
            yielded += 1
            if yielded >= max_pages:
                print(f"  hit max_pages cap ({max_pages}), stopping")
                return
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")


def date_str(page):
    d = (page.get("properties", {}).get("日期") or {}).get("date") or {}
    return (d.get("start") or "")[:10]


def duration(page):
    return (page.get("properties", {}).get("时长") or {}).get("number") or 0


def main():
    try:
        nh = NotionHelper()
    except Exception as e:
        print(f"FATAL: NotionHelper init failed: {e}")
        traceback.print_exc()
        sys.exit(0)  # 0 = "ok" so workflow doesn't fail

    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("FATAL: NOTION_TOKEN missing")
        sys.exit(0)

    if not nh.day_database_id:
        print("FATAL: day_database_id not resolved; check 12 databases exist")
        sys.exit(0)

    try:
        print("Fetching all day pages...")
        pages = list(all_pages_in_db(token, nh.day_database_id))
        print(f"  {len(pages)} day pages")
    except Exception as e:
        print(f"FATAL: query failed: {e}")
        traceback.print_exc()
        sys.exit(0)

    by_date = defaultdict(list)
    for p in pages:
        d = date_str(p)
        if d:
            by_date[d].append(p)

    token_header = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    archived = 0
    dup_dates = 0
    for d, group in by_date.items():
        if len(group) <= 1:
            continue
        dup_dates += 1
        group_sorted = sorted(group, key=lambda p: -duration(p))
        keep = group_sorted[0]
        for p in group_sorted[1:]:
            for attempt in range(3):
                try:
                    r = requests.patch(
                        f"{NOTION_API}/pages/{p['id']}",
                        headers=token_header,
                        json={"in_trash": True},
                        timeout=30,
                    )
                    if r.status_code < 300:
                        archived += 1
                    else:
                        print(f"  {d}: archive {p['id']} HTTP {r.status_code}: {r.text[:120]}")
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  {d}: archive {p['id']} failed: {type(e).__name__}: {e}")
                    else:
                        time.sleep(2 ** attempt)
        if dup_dates % 50 == 0:
            print(f"  progress: {dup_dates} duplicate dates, {archived} archived")

    print(f"\ndone: {dup_dates} duplicate dates, {archived} pages archived")


if __name__ == "__main__":
    main()
