"""One-time migration: 给年/月/周 database 加「总阅读时长（格式化）」formula 字段。

新加的「总阅读时长」是 number 字段（单位：分钟），人看着不直观。
加一个 formula 字段把分钟渲染成 "1h 23m" / "46d 14h" 这样的友好格式。

公式：
    if(minutes >= 1440,
       "{d}d {h}h",            // ≥ 1 day
       if(minutes >= 60,
          "{h}h {m}m",          // ≥ 1 hour
          "{m}m"))              // < 1 hour

幂等：跑第二次会跳过（formula 字段已存在）。

用法：
    python3 scripts/migrate_to_duration.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import requests  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from weread2notionpro.notion_helper import NotionHelper  # noqa: E402
from setup_databases import make_headers  # noqa: E402

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"  # 2025+ 才有 formula / data_source 端点
PROP_NAME = "总阅读时长"
FORMULA_NAME = "总阅读时长（格式化）"

FORMULA_EXPR = (
    f'if(empty(prop("{PROP_NAME}")), "", '
    f'if(prop("{PROP_NAME}") == 0, "", '
    f'if(prop("{PROP_NAME}") >= 1440, '
    f'format(floor(prop("{PROP_NAME}") / 1440)) + "d " + '
    f'format(floor(mod(prop("{PROP_NAME}"), 1440) / 60)) + "h", '
    f'if(prop("{PROP_NAME}") >= 60, '
    f'format(floor(prop("{PROP_NAME}") / 60)) + "h " + '
    f'format(mod(prop("{PROP_NAME}"), 60)) + "m", '
    f'format(prop("{PROP_NAME}")) + "m"))))'
)


def get_data_source_id(token, db_id):
    """Notion 2025+ API：database 有 data_source；schema 在 data_source 上。"""
    r = requests.get(
        f"{NOTION_API}/databases/{db_id}",
        headers={**make_headers(token), "Notion-Version": NOTION_VERSION},
    )
    r.raise_for_status()
    data = r.json()
    ds = data.get("data_sources") or []
    if not ds:
        raise SystemExit(f"找不到 data_source: {db_id}")
    return ds[0]["id"]


def get_props(token, ds_id):
    r = requests.get(
        f"{NOTION_API}/data_sources/{ds_id}",
        headers={**make_headers(token), "Notion-Version": NOTION_VERSION},
    )
    r.raise_for_status()
    return r.json().get("properties", {})


def add_formula(token, ds_id, prop_name, expr):
    body = {"properties": {prop_name: {"formula": {"expression": expr}}}}
    r = requests.patch(
        f"{NOTION_API}/data_sources/{ds_id}",
        headers={**make_headers(token), "Notion-Version": NOTION_VERSION,
                 "Content-Type": "application/json"},
        json=body,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(
            f"加 formula 失败: {ds_id}.{prop_name} "
            f"status={r.status_code} body={r.text[:300]}"
        )


def main():
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise SystemExit("需要 NOTION_TOKEN env（或写在 .env 里）")

    nh = NotionHelper()
    targets = [
        ("年", nh.year_database_id),
        ("月", nh.month_database_id),
        ("周", nh.week_database_id),
    ]

    for label, db_id in targets:
        if not db_id:
            print(f"  跳过 {label}（没拿到 db id）")
            continue
        ds_id = get_data_source_id(token, db_id)
        props = get_props(token, ds_id)
        print(f"--- {label} (data_source: {ds_id}) ---")
        # 确保 number 字段
        if PROP_NAME not in props or props[PROP_NAME].get("type") != "number":
            print(f"  缺少 {PROP_NAME} number 字段，跳过（先跑 setup_databases.py 建库）")
            continue
        # 公式
        if FORMULA_NAME in props and props[FORMULA_NAME].get("type") == "formula":
            print(f"  {FORMULA_NAME} 已存在，跳过")
            continue
        print(f"  加 {FORMULA_NAME} formula...")
        add_formula(token, ds_id, FORMULA_NAME, FORMULA_EXPR)
        print(f"  OK")

    print("\n=== 完成 ===")
    print(f"现在：往 number 字段写分钟数，formula 字段会自动渲染 '1h 23m' / '46d 14h'")
    print(f"接下来跑：python3 scripts/aggregate_durations.py")


if __name__ == "__main__":
    main()
