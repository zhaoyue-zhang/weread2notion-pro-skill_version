"""Dry-run: 验证 WEREAD_API_KEY 能调通，不动 Notion。

用法：
    export WEREAD_API_KEY="wrk-你的key"
    python scripts/dry_run.py
"""
import os
import sys
import json
from pathlib import Path

# 让脚本能从仓库根目录 import weread2notionpro 包
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from weread2notionpro.weread_api import WeReadApi  # noqa: E402


def banner(title):
    print("\n" + "=" * 60)
    print(f" {title}")
    print("=" * 60)


def summary(label, value, max_len=120):
    s = repr(value)
    if len(s) > max_len:
        s = s[:max_len] + f"... (+{len(s) - max_len} chars)"
    print(f"  {label}: {s}")


def main():
    api_key = os.getenv("WEREAD_API_KEY")
    if not api_key:
        print("ERROR: WEREAD_API_KEY 没设。")
        print("  export WEREAD_API_KEY=wrk-你的key")
        sys.exit(1)

    print(f"使用 API Key: {api_key[:8]}...{api_key[-4:]}")

    try:
        api = WeReadApi()
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # 1. 笔记本列表
    banner("1. get_notebooklist() — 你的笔记本概览")
    try:
        books = api.get_notebooklist()
        if books is None:
            print("  返回 None（可能被风控或 Key 失效）")
        else:
            summary("数量", len(books))
            print("  前 3 本：")
            for b in books[:3]:
                title = b.get("book", {}).get("title", "?")
                author = b.get("book", {}).get("author", "?")
                note = b.get("noteCount", 0)
                rev = b.get("reviewCount", 0)
                print(f"    - 《{title}》 by {author} | 划线 {note} 想法 {rev}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return

    if not books:
        print("\n没找到任何有笔记的书，先去微信读书划几条线再来测。")
        return

    # 2. 用第一本书试剩下所有接口
    first_book_id = books[0]["book"]["bookId"]
    first_title = books[0]["book"]["title"]
    print(f"\n下面用第一本书《{first_title}》({first_book_id}) 测试剩余接口：")

    # 2a. 划线
    banner(f"2. get_bookmark_list({first_book_id}) — 划线")
    try:
        bookmarks = api.get_bookmark_list(first_book_id)
        summary("条数", len(bookmarks) if bookmarks else 0)
        if bookmarks:
            summary("首条", bookmarks[0])
    except Exception as e:
        print(f"  FAILED: {e}")

    # 2b. 想法
    banner(f"3. get_review_list({first_book_id}) — 想法/点评")
    try:
        reviews = api.get_review_list(first_book_id)
        summary("条数", len(reviews) if reviews else 0)
        if reviews:
            summary("首条", reviews[0])
    except Exception as e:
        print(f"  FAILED: {e}")

    # 2c. 章节
    banner(f"4. get_chapter_info({first_book_id}) — 章节")
    try:
        chapters = api.get_chapter_info(first_book_id)
        summary("章节数", len(chapters) if chapters else 0)
        if chapters:
            first_uid = next(iter(chapters))
            summary("首章样例", chapters[first_uid])
    except Exception as e:
        print(f"  FAILED: {e}")

    # 2d. 书籍详情
    banner(f"5. get_bookinfo({first_book_id}) — 书籍详情")
    try:
        info = api.get_bookinfo(first_book_id)
        if info:
            print(f"  标题: {info.get('title')}")
            print(f"  作者: {info.get('author')}")
            print(f"  ISBN: {info.get('isbn')}")
            print(f"  分类: {info.get('categories')}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # 2e. 阅读进度
    banner(f"6. get_read_info({first_book_id}) — 阅读进度")
    try:
        info = api.get_read_info(first_book_id)
        summary("返回", info)
    except Exception as e:
        print(f"  FAILED: {e}")

    # 3. 阅读时长历史
    banner("7. get_api_data() — 阅读时长历史（按日分桶）")
    try:
        data = api.get_api_data()
        rts = data.get("readTimes") or {}
        summary("天数", len(rts))
        if rts:
            sorted_days = sorted(rts.items(), key=lambda kv: -kv[1])[:5]
            print("  阅读时长 Top 5 天：")
            for ts, secs in sorted_days:
                from datetime import datetime, timedelta
                d = datetime.utcfromtimestamp(int(ts)) + timedelta(hours=8)
                print(f"    {d.strftime('%Y-%m-%d')} → {secs} 秒 ({secs // 60} 分钟)")
    except Exception as e:
        print(f"  FAILED: {e}")

    # 4. 书架
    banner("8. get_bookshelf() — 书架")
    try:
        shelf = api.get_bookshelf()
        if isinstance(shelf, dict):
            summary("返回 keys", list(shelf.keys()))
            summary("books 条数", len(shelf.get("books") or []))
    except Exception as e:
        print(f"  FAILED: {e}")

    print("\n" + "=" * 60)
    print(" Dry-run 跑完。所有方法都没报 FAILED 就可以配 Notion 跑完整同步。")
    print("=" * 60)


if __name__ == "__main__":
    main()
