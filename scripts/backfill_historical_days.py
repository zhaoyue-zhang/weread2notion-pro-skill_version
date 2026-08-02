"""Backfill historical years' reading time into the 「日」 database.

read_time.py only writes the current year. This script backfills prior
years (default: 5 years back) so the year/month/week aggregation in
Notion has data for every year you've read.

Read the official /readdata/detail for each (year, month) combination,
then upsert into the 「日」 database by timestamp (one row per day,
relation to the appropriate year/month/week page).

Safe to re-run (idempotent: same timestamp → same row).
"""
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.weread_api import WeReadApi
from weread2notionpro.utils import get_property_value


def main():
    nh = NotionHelper()
    api = WeReadApi()

    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    cur_year = datetime.now(tz).year
    # Historical years: 5 years back through last year (this year is
    # handled by read_time.py). Override with BACKFILL_YEARS env to narrow.
    override = os.getenv("BACKFILL_YEARS")
    if override:
        years = [int(y) for y in override.split(",")]
    else:
        years = list(range(cur_year - 5, cur_year))

    # 1) Pull all the data in one batch via the gateway
    print(f"Fetching {len(years)} years of daily data...")
    daily = api.get_daily_data(years=years)
    read_times = {int(k): int(v) for k, v in daily.get("readTimes", {}).items() if int(v) > 0}
    print(f"  got {len(read_times)} non-zero days")

    # 2) Pull existing day pages, keyed by timestamp
    print("Fetching existing day pages from Notion...")
    existing = {}  # ts -> page_id
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = nh.client.databases.query(database_id=nh.day_database_id, **body)
        for p in r.get("results", []):
            ts = get_property_value(p.get("properties", {}).get("时间戳"))
            if ts is not None:
                existing[int(ts)] = p.get("id")
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    print(f"  found {len(existing)} existing day pages")

    # 3) Upsert each day
    written = 0
    skipped = 0
    for ts, secs in sorted(read_times.items()):
        from datetime import datetime
        dt = datetime.fromtimestamp(ts, tz=tz)
        if dt.year not in years:
            continue  # this year is read_time's job

        # Find or create relation ids for year/month/week
        # (cache to avoid creating dupes)
        # Use the existing helper from read_time.py
        from weread2notionpro.read_time import insert_to_notion

        page_id = existing.get(ts)
        if page_id is None:
            # No existing page — insert_to_notion will create one
            insert_to_notion(None, ts, secs)
            written += 1
        else:
            # Existing page — patch the 「时长」 number (only if changed)
            cur = get_property_value(
                nh.client.pages.retrieve(page_id=page_id)
                .get("properties", {})
                .get("时长")
            )
            if cur != secs:
                nh.client.pages.update(
                    page_id=page_id,
                    properties={"时长": {"number": secs}},
                )
                written += 1
            else:
                skipped += 1

        if written % 50 == 0 and written > 0:
            print(f"  progress: {written} written, {skipped} skipped")

    print(f"\ndone: {written} written/updated, {skipped} skipped")


if __name__ == "__main__":
    main()
