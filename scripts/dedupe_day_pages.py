"""Deduplicate the 「日」 database.

Background: read_time.py previously used get_api_data() (annually mode)
which wrote 12 monthly bucket pages per year keyed by UTC-0 month-start
timestamps. After we switched to get_daily_data() (monthly mode) which
writes per-day Shanghai-0 timestamps, the same calendar date ends up
with two day-pages (one with 0s, one with the real value).

This script groups day pages by their 日期 (calendar date) and deletes
all but the longest-duration page in each group.

Safe to re-run.
"""
import os
import sys
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


def all_pages_in_db(token, db_id):
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
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
        for p in data.get("results", []):
            yield p
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")


def date_str(page):
    d = (page.get("properties", {}).get("日期") or {}).get("date") or {}
    return (d.get("start") or "")[:10]


def duration(page):
    return (page.get("properties", {}).get("时长") or {}).get("number") or 0


def main():
    nh = NotionHelper()
    token = os.getenv("NOTION_TOKEN")

    print("Fetching all day pages...")
    pages = list(all_pages_in_db(token, nh.day_database_id))
    print(f"  {len(pages)} day pages")

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
            r = requests.patch(
                f"{NOTION_API}/pages/{p['id']}",
                headers=token_header,
                json={"in_trash": True},
                timeout=30,
            )
            if r.status_code < 300:
                archived += 1
            else:
                print(f"  {d}: failed to archive {p['id']}: {r.status_code} {r.text[:200]}")
        if dup_dates % 50 == 0:
            print(f"  progress: {dup_dates} duplicate dates, {archived} archived")

    print(f"\ndone: {dup_dates} duplicate dates, {archived} pages archived")


if __name__ == "__main__":
    main()
