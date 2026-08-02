"""Aggregate per-day reading time into year / month / week pages.

读「日」database 的每行（每天），按其「年」/「月」/「周」relation 关系，
把「时长」累加写到对应 year/month/week page 的「总阅读时长」字段。

单位：分钟（整数）。Notion 用「总阅读时长（格式化）」formula 字段自动渲染成
"1h 23m" / "46d 14h" 这样的友好格式。

可以直接反复跑，幂等。所有异常都吞掉，best-effort — 失败也不会让 workflow
挂掉（read_time 数据本身已经写入「日」database，下次跑能修）。
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
from weread2notionpro.utils import get_property_value

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def all_pages_in_db(token, db_id, max_pages=20000):
    """Yield every page in a database (handles pagination + retry).

    max_pages is a safety cap: > 20000 day pages means something's very
    wrong, no point continuing to query.
    """
    cursor = None
    yielded = 0
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
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


def relation_id(prop):
    """Return first page id from a relation property (or None)."""
    if not prop or prop.get("type") != "relation":
        return None
    rels = prop.get("relation") or []
    if not rels:
        return None
    return rels[0].get("id")


def main():
    try:
        nh = NotionHelper()
    except Exception as e:
        print(f"FATAL: NotionHelper init failed: {e}")
        traceback.print_exc()
        return

    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("FATAL: NOTION_TOKEN missing")
        return

    if not (nh.year_database_id and nh.month_database_id and nh.week_database_id and nh.day_database_id):
        print("FATAL: db id not resolved; check 12 databases exist")
        return

    # 1) Pull all day pages — each row has 「时长」+ 「年」/「月」/「周」relations
    try:
        print("Fetching all day pages...")
        days = list(all_pages_in_db(token, nh.day_database_id))
        print(f"  found {len(days)} day pages")
    except Exception as e:
        print(f"FATAL: query failed: {e}")
        traceback.print_exc()
        return

    # 2) Aggregate by relation target
    year_secs = defaultdict(int)
    month_secs = defaultdict(int)
    week_secs = defaultdict(int)
    for d in days:
        props = d.get("properties") or {}
        secs = get_property_value(props.get("时长"))
        if not secs:
            continue
        try:
            secs = int(secs)
        except (TypeError, ValueError):
            continue

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
    token_header = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    def patch_minutes(page_id, mins):
        for attempt in range(3):
            try:
                r = requests.patch(
                    f"{NOTION_API}/pages/{page_id}",
                    headers=token_header,
                    json={"properties": {"总阅读时长": {"number": mins}}},
                    timeout=30,
                )
                return r.status_code
            except Exception as e:
                if attempt == 2:
                    print(f"    patch {page_id} failed: {type(e).__name__}: {e}")
                    return 0
                time.sleep(2 ** attempt)

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
        # Be polite to Notion rate limit (3 req/s)
        if i % 10 == 0:
            time.sleep(0.05)
    print(f"  patching {n_month} months...")
    for i, (page_id, secs) in enumerate(month_secs.items(), 1):
        minutes = max(1, round(secs / 60))
        s = patch_minutes(page_id, minutes)
        if s >= 400:
            fails.append(f"month {page_id}: HTTP {s}")
        if i % 50 == 0:
            print(f"    {i}/{n_month}")
        if i % 10 == 0:
            time.sleep(0.05)
    print(f"  patching {n_week} weeks...")
    for i, (page_id, secs) in enumerate(week_secs.items(), 1):
        minutes = max(1, round(secs / 60))
        s = patch_minutes(page_id, minutes)
        if s >= 400:
            fails.append(f"week {page_id}: HTTP {s}")
        if i % 50 == 0:
            print(f"    {i}/{n_week}")
        if i % 10 == 0:
            time.sleep(0.05)

    if fails:
        print(f"  {len(fails)} failed PATCHes:")
        for f in fails[:10]:
            print(f"    {f}")
        if len(fails) > 10:
            print(f"    ... and {len(fails) - 10} more")
    print("done.")


if __name__ == "__main__":
    main()
