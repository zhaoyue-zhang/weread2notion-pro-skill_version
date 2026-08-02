"""Aggregate per-day reading time into year / month / week pages.

依赖：年/月/周 database 已经有「总阅读时长」number 字段
（用 scripts/add_aggregation_fields.py 一次性加上）。

读「日」database 的每行（每天），按其「年」/「月」/「周」relation 关系，
把「时长」累加写到对应 year/month/week page 的「总阅读时长」字段。

单位：
- 年  : 小时（小数，保留 1 位）
- 月  : 分钟（整数）
- 周  : 分钟（整数）

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


def page_title(page):
    """Pull the title text out of any Notion page dict."""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            rich = prop.get("title") or []
            return "".join(t.get("plain_text", "") for t in rich)
    return ""


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

    # 1) Make sure aggregation fields exist on year/month/week. Idempotent.
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from setup_databases import add_number_property
    except ImportError as e:
        print(f"could not import setup_databases.add_number_property: {e}")
        add_number_property = None
    if add_number_property:
        for db_id, name in (
            (nh.year_database_id, "总阅读时长"),
            (nh.month_database_id, "总阅读时长"),
            (nh.week_database_id, "总阅读时长"),
        ):
            if db_id:
                try:
                    add_number_property(token, db_id, name)
                    print(f"  ensured {name} on {db_id[:20]}")
                except SystemExit as e:
                    if "already exists" in str(e):
                        print(f"  {name} already present on {db_id[:20]}")
                    else:
                        print(f"  warn: {db_id[:20]} {e}")
                        continue

    # 2) Pull all day pages — each row has 「时长」+ 「年」/「月」/「周」relations
    print("Fetching all day pages...")
    days = list(all_pages_in_db(token, nh.day_database_id))
    print(f"  found {len(days)} day pages")

    # 3) Aggregate
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

    print(f"  aggregated → {len(year_secs)} years, {len(month_secs)} months, {len(week_secs)} weeks")

    # 4) Patch each year/month/week page with the new field
    def patch(token, page_id, value):
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            json={"properties": {"总阅读时长": {"number": value}}},
            timeout=30,
        )
        return r.status_code

    for page_id, secs in year_secs.items():
        hours = round(secs / 3600, 1)
        s = patch(token, page_id, hours)
        if s >= 400:
            print(f"  year {page_id}: HTTP {s}")
    for page_id, secs in month_secs.items():
        minutes = round(secs / 60)
        s = patch(token, page_id, minutes)
        if s >= 400:
            print(f"  month {page_id}: HTTP {s}")
    for page_id, secs in week_secs.items():
        minutes = round(secs / 60)
        s = patch(token, page_id, minutes)
        if s >= 400:
            print(f"  week {page_id}: HTTP {s}")

    print("done.")


if __name__ == "__main__":
    main()
