"""Aggregate per-day reading time into year / month / week pages.

读「日」database 的每行（每天），按其「年」/「月」/「周」relation 关系，
把「时长」累加写到对应 year/month/week page 的「总阅读时长」字段。

单位：分钟（整数）。Notion 用「总阅读时长（格式化）」formula 字段自动渲染成
"1h 23m" / "46d 14h" 这样的友好格式。

可以直接反复跑，幂等。
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
from weread2notionpro.utils import get_property_value

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def all_pages_in_db(token, db_id):
    """Yield every page in a database (handles pagination)."""
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


def relation_id(prop):
    """Return first page id from a relation property (or None)."""
    if not prop or prop.get("type") != "relation":
        return None
    rels = prop.get("relation") or []
    if not rels:
        return None
    return rels[0].get("id")


def main():
    nh = NotionHelper()
    token = os.getenv("NOTION_TOKEN")

    # 1) Pull all day pages — each row has 「时长」+ 「年」/「月」/「周」relations
    print("Fetching all day pages...")
    days = list(all_pages_in_db(token, nh.day_database_id))
    print(f"  found {len(days)} day pages")

    # 2) Aggregate by relation target
    year_secs = defaultdict(int)
    month_secs = defaultdict(int)
    week_secs = defaultdict(int)
    for d in days:
        props = d.get("properties") or {}
        secs = get_property_value(props.get("时长"))
        if not secs:
            continue
        secs = int(secs)

        yid = relation_id(props.get("年"))
        if yid:
            year_secs[yid] += secs

        mid = relation_id(props.get("月"))
        if mid:
            month_secs[mid] += secs

        wid = relation_id(props.get("周"))
        if wid:
            week_secs[wid] += secs

    print(
        f"  aggregated → {len(year_secs)} years, "
        f"{len(month_secs)} months, {len(week_secs)} weeks"
    )

    # 3) Patch each year/month/week page. The number field stores minutes
    #    (integer); the 公式 column auto-renders "1h 23m" / "46d 14h".
    def patch_minutes(page_id, mins):
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            json={"properties": {"总阅读时长": {"number": mins}}},
            timeout=30,
        )
        return r.status_code

    fails = []
    n_year, n_month, n_week = len(year_secs), len(month_secs), len(week_secs)
    print(f"  patching {n_year} years...")
    for i, (page_id, secs) in enumerate(year_secs.items(), 1):
        minutes = max(1, round(secs / 60))  # at least 1 to avoid 0m showing nothing
        s = patch_minutes(page_id, minutes)
        if s >= 400:
            fails.append(f"year {page_id}: HTTP {s}")
        if i % 50 == 0:
            print(f"    {i}/{n_year}")
    print(f"  patching {n_month} months...")
    for i, (page_id, secs) in enumerate(month_secs.items(), 1):
        minutes = max(1, round(secs / 60))
        s = patch_minutes(page_id, minutes)
        if s >= 400:
            fails.append(f"month {page_id}: HTTP {s}")
        if i % 50 == 0:
            print(f"    {i}/{n_month}")
    print(f"  patching {n_week} weeks...")
    for i, (page_id, secs) in enumerate(week_secs.items(), 1):
        minutes = max(1, round(secs / 60))
        s = patch_minutes(page_id, minutes)
        if s >= 400:
            fails.append(f"week {page_id}: HTTP {s}")
        if i % 50 == 0:
            print(f"    {i}/{n_week}")

    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
    print("done.")


if __name__ == "__main__":
    main()
