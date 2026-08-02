"""一键在 Notion 里建出 weread2notion-pro 需要的 9 个 database。

代码运行时会自己建"阅读记录"和"设置"两个 database，所以这里只建 9 个。

⚠️ 实现说明：直接用 requests 调 Notion REST API（Notion-Version: 2022-06-28），
不依赖 notion_client 库的 databases.create（v2.5+ 把 properties 从 pick 白名单
里去掉了，会导致 schema 丢字段）。

用法：
    export NOTION_TOKEN="secret_xxx"  # 写到 .env 也行
    export NOTION_PAGE="https://www.notion.so/your-page-url"
    python3 scripts/setup_databases.py

可选：
    --dry-run    只打印会建什么，不调 Notion API
    --force      已存在同名 database 仍创建（会产生重复，不推荐）
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import requests  # noqa: E402

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"  # 旧 API：databases.create 接受 properties+database_id


# ---- Database schema definitions --------------------------------------------

ICON_AUTHOR = "https://www.notion.so/icons/user-circle-filled_gray.svg"
ICON_CATEGORY = "https://www.notion.so/icons/tag_gray.svg"
ICON_BOOK = "https://www.notion.so/icons/book_gray.svg"
ICON_BOOKMARK = "https://www.notion.so/icons/bookmark_gray.svg"
ICON_REVIEW = "https://www.notion.so/icons/chat_bubble_gray.svg"
ICON_CHAPTER = "https://www.notion.so/icons/list_gray.svg"
ICON_TIME = "https://www.notion.so/icons/target_gray.svg"


def schema_category():
    return {"title": "分类", "icon": ICON_CATEGORY,
            "properties": {"标题": {"title": {}}}}


def schema_author():
    return {"title": "作者", "icon": ICON_AUTHOR,
            "properties": {"标题": {"title": {}}}}


def schema_book():
    return {
        "title": "书架", "icon": ICON_BOOK,
        "properties": {
            "书名": {"title": {}},
            "BookId": {"rich_text": {}},
            "Sort": {"number": {}},
            "ISBN": {"rich_text": {}},
            "链接": {"url": {}},
            "评分": {"number": {}},
            "封面": {"files": {}},
            "阅读状态": {
                "status": {
                    "options": [
                        {"name": "想读", "color": "gray"},
                        {"name": "在读", "color": "blue"},
                        {"name": "已读", "color": "green"},
                    ]
                }
            },
            "阅读时长": {"number": {}},
            "阅读进度": {"number": {}},
            "阅读天数": {"number": {}},
            "时间": {"date": {}},
            "开始阅读时间": {"date": {}},
            "最后阅读时间": {"date": {}},
            "简介": {"rich_text": {}},
            "书架分类": {"select": {}},
            "我的评分": {"select": {}},
            "豆瓣链接": {"url": {}},
            # relation 字段在第二阶段用 update 加上
            "作者": _rel_placeholder(),
            "分类": _rel_placeholder(),
            "年": _rel_placeholder(),
            "月": _rel_placeholder(),
            "周": _rel_placeholder(),
            "日": _rel_placeholder(),
        },
    }


def schema_bookmark():
    return {
        "title": "划线", "icon": ICON_BOOKMARK,
        "properties": {
            "Name": {"title": {}},
            "bookId": {"rich_text": {}},
            "bookmarkId": {"rich_text": {}},
            "blockId": {"rich_text": {}},
            "range": {"rich_text": {}},
            "chapterUid": {"number": {}},
            "bookVersion": {"number": {}},
            "colorStyle": {"number": {}},
            "type": {"number": {}},
            "style": {"number": {}},
            "abstract": {"rich_text": {}},
            "Date": {"date": {}},
            # relation 字段在第二阶段用 update 加上
            "书籍": _rel_placeholder(),
            "年": _rel_placeholder(),
            "月": _rel_placeholder(),
            "周": _rel_placeholder(),
            "日": _rel_placeholder(),
        },
    }


def schema_review():
    return {
        "title": "笔记", "icon": ICON_REVIEW,
        "properties": {
            "Name": {"title": {}},
            "bookId": {"rich_text": {}},
            "reviewId": {"rich_text": {}},
            "blockId": {"rich_text": {}},
            "range": {"rich_text": {}},
            "chapterUid": {"number": {}},
            "bookVersion": {"number": {}},
            "type": {"number": {}},
            "star": {"number": {}},
            "abstract": {"rich_text": {}},
            "Date": {"date": {}},
            "书籍": _rel_placeholder(),
            "年": _rel_placeholder(),
            "月": _rel_placeholder(),
            "周": _rel_placeholder(),
            "日": _rel_placeholder(),
        },
    }


def schema_chapter():
    return {
        "title": "章节", "icon": ICON_CHAPTER,
        "properties": {
            "Name": {"title": {}},
            "blockId": {"rich_text": {}},
            "chapterUid": {"number": {}},
            "chapterIdx": {"number": {}},
            "readAhead": {"number": {}},
            "updateTime": {"number": {}},
            "level": {"number": {}},
            "书籍": _rel_placeholder(),
        },
    }


def schema_year():
    return {"title": "年", "icon": ICON_TIME,
            "properties": {
                "标题": {"title": {}},
                "日期": {"date": {}},
                # 总阅读时长（单位：小时，小数；由 aggregate_durations.py 回填）
                "总阅读时长": {"number": {"format": "number"}},
            }}


def schema_month():
    return {"title": "月", "icon": ICON_TIME,
            "properties": {
                "标题": {"title": {}},
                "日期": {"date": {}},
                # 总阅读时长（单位：分钟，整数；由 aggregate_durations.py 回填）
                "总阅读时长": {"number": {"format": "number"}},
                "年": _rel_placeholder(),
            }}


def schema_week():
    return {"title": "周", "icon": ICON_TIME,
            "properties": {
                "标题": {"title": {}},
                "日期": {"date": {}},
                # 总阅读时长（单位：分钟，整数；由 aggregate_durations.py 回填）
                "总阅读时长": {"number": {"format": "number"}},
                "年": _rel_placeholder(),
                "月": _rel_placeholder(),
            }}


def schema_day():
    return {"title": "日", "icon": ICON_TIME,
            "properties": {
                "标题": {"title": {}},
                "日期": {"date": {}},
                "时间戳": {"number": {}},
                "时长": {"number": {}},
                "年": _rel_placeholder(),
                "月": _rel_placeholder(),
                "周": _rel_placeholder(),
            }}


def _rel_placeholder():
    """relation 占位符。建库时跳过，第二阶段用 update 加上。"""
    return {"__RELATION_PLACEHOLDER__": True}


# Build order: 独立 entity → 主表 → 时间维度 → 内容表
BUILD_ORDER = [
    ("分类", schema_category, []),
    ("作者", schema_author, []),
    ("书架", schema_book, ["分类", "作者"]),
    ("年", schema_year, []),
    ("月", schema_month, ["年"]),
    ("周", schema_week, ["年", "月"]),
    ("日", schema_day, ["年", "月", "周"]),
    ("划线", schema_bookmark, ["书架", "年", "月", "周"]),
    ("笔记", schema_review, ["书架", "年", "月", "周"]),
    ("章节", schema_chapter, ["书架"]),
]


# ---- REST API helpers ------------------------------------------------------

def make_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def extract_page_id(notion_url):
    m = re.search(
        r"([a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})",
        notion_url,
    )
    if not m:
        raise SystemExit(f"无法从 URL 提取 page id: {notion_url!r}")
    return m.group(0)


def find_existing_child_databases(token, page_id):
    out = {}
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(
            f"{NOTION_API}/blocks/{page_id}/children",
            headers=make_headers(token), params=params,
        )
        if r.status_code != 200:
            raise SystemExit(f"列出子块失败: {r.status_code} {r.text[:200]}")
        data = r.json()
        for child in data.get("results", []):
            if child.get("type") == "child_database":
                title = child.get("child_database", {}).get("title", "")
                out[title] = child.get("id")
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def strip_placeholders(properties):
    """Remove relation placeholders; they get added in pass 2."""
    out = {}
    for name, defn in properties.items():
        if isinstance(defn, dict) and defn.get("__RELATION_PLACEHOLDER__"):
            continue
        out[name] = defn
    return out


def create_database(token, page_id, schema):
    body = {
        "parent": {"page_id": page_id, "type": "page_id"},
        "title": [{"type": "text", "text": {"content": schema["title"]}}],
        "icon": {"type": "external", "external": {"url": schema["icon"]}},
        "properties": strip_placeholders(schema["properties"]),
    }
    r = requests.post(
        f"{NOTION_API}/databases",
        headers=make_headers(token), json=body,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(
            f"建库失败: {schema['title']} status={r.status_code} body={r.text[:400]}"
        )
    return r.json()["id"]


def add_relation_property(token, db_id, prop_name, target_db_id):
    """Add a relation property to an existing database."""
    body = {
        "properties": {
            prop_name: {
                "relation": {
                    "database_id": target_db_id,
                    "type": "single_property",
                    "single_property": {},
                }
            }
        }
    }
    r = requests.patch(
        f"{NOTION_API}/databases/{db_id}",
        headers=make_headers(token), json=body,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(
            f"加 relation 失败: {db_id}.{prop_name} -> {target_db_id} "
            f"status={r.status_code} body={r.text[:300]}"
        )


def add_number_property(token, db_id, prop_name, fmt="number"):
    """Add a number property to an existing database.

    Idempotent: if the property already exists, this is a no-op (the
    PATCH call returns 200 with the existing schema).
    """
    body = {"properties": {prop_name: {"number": {"format": fmt}}}}
    r = requests.patch(
        f"{NOTION_API}/databases/{db_id}",
        headers=make_headers(token), json=body,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(
            f"加 number 失败: {db_id}.{prop_name} "
            f"status={r.status_code} body={r.text[:300]}"
        )


# ---- Main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划，不调 Notion API")
    ap.add_argument("--force", action="store_true",
                    help="已存在同名 database 时仍创建")
    args = ap.parse_args()

    if args.dry_run:
        print("DRY-RUN: 不会调任何 Notion API\n")
        for title, schema_fn, _ in BUILD_ORDER:
            schema = schema_fn()
            n = len(schema["properties"])
            rels = [p for p, v in schema["properties"].items()
                    if isinstance(v, dict) and v.get("__RELATION_PLACEHOLDER__")]
            print(f"  - {schema['title']}  ({n} 属性, {len(rels)} relation)")
            for r in rels:
                print(f"      relation: {r}")
        print("\nDRY-RUN 完毕。")
        return

    token = os.getenv("NOTION_TOKEN")
    page_url = os.getenv("NOTION_PAGE")
    if not token or not page_url:
        raise SystemExit(
            "需要 NOTION_TOKEN 和 NOTION_PAGE 环境变量（或写在 .env 里）。\n"
            "  export NOTION_TOKEN=secret_xxx\n"
            "  export NOTION_PAGE='https://app.notion.com/p/...'"
        )
    page_id = extract_page_id(page_url)

    print(f"目标 page: {page_id}")
    print(f"Notion API version: {NOTION_VERSION}")
    print("扫描已有 database...")
    existing = find_existing_child_databases(token, page_id)
    if existing:
        print(f"  发现 {len(existing)} 个已存在:")
        for t in existing:
            print(f"    - {t}")

    db_ids = {}
    skipped, created = [], []

    # 第一阶段：建库（relation 字段占位但不连接）
    for title, schema_fn, _ in BUILD_ORDER:
        schema = schema_fn()
        if title in existing and not args.force:
            print(f"  跳过 {title}（已存在: {existing[title]}）")
            db_ids[title] = existing[title]
            skipped.append(title)
            continue
        print(f"  建 {title}...", end="", flush=True)
        try:
            db_id = create_database(token, page_id, schema)
            db_ids[title] = db_id
            created.append(title)
            print(f" OK ({db_id})")
        except SystemExit as e:
            print(f" FAILED: {e}")
            raise
        time.sleep(0.3)

    # 第二阶段：连接 relation 字段
    print("\n连接 relation 字段...")
    REL_MAPPING = {
        "书籍": "书架",
        "作者": "作者",
        "分类": "分类",
        "年": "年",
        "月": "月",
        "周": "周",
        "日": "日",
        "书架": "书架",  # 书架的"年/月/周/日" relation 自身指向时间维度
    }
    for title, schema_fn, _ in BUILD_ORDER:
        schema = schema_fn()
        db_id = db_ids.get(title)
        if not db_id:
            continue
        for prop_name, prop_def in schema["properties"].items():
            if not (isinstance(prop_def, dict)
                    and prop_def.get("__RELATION_PLACEHOLDER__")):
                continue
            target = REL_MAPPING.get(prop_name)
            target_id = db_ids.get(target) if target else None
            if not target_id:
                print(f"  ! 跳过 {title}.{prop_name}（目标 {target} 未建）")
                continue
            print(f"  {title}.{prop_name} -> {target}", end="", flush=True)
            try:
                add_relation_property(token, db_id, prop_name, target_id)
                print(" OK")
            except SystemExit as e:
                print(f" FAILED: {e}")
                raise
            time.sleep(0.3)

    print("\n=== 完成 ===")
    print(f"  新建: {len(created)} 个 ({', '.join(created) or '无'})")
    print(f"  跳过（已存在）: {len(skipped)} 个 ({', '.join(skipped) or '无'})")
    print(f"\n数据库 ID 列表：")
    for title, _, _ in BUILD_ORDER:
        print(f"  {title}: {db_ids.get(title)}")
    print(f"\n接下来：")
    print(f"  1) 这两个 database 由代码自动建：'阅读记录'、'设置'")
    print(f"  2) 第一次跑同步时，notion_helper.py 会自动建上面两个")
    print(f"  3) 在 GitHub Secrets / env 配 NOTION_TOKEN + NOTION_PAGE，跑 weread 同步")


if __name__ == "__main__":
    main()
