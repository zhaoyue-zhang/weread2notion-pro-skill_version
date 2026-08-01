"""WeRead API client backed by the official WeRead Agent API Gateway.

This replaces the legacy private API + Cookie auth with the official
gateway + WEREAD_API_KEY, which is the path recommended by WeRead's
"weread-skills" official Skill.

Public method signatures are kept identical to the previous implementation
so the rest of the project (book.py, weread.py, read_time.py) keeps working
without changes.
"""
import os
import re

import requests
from retrying import retry

# Official Agent API Gateway
WEREAD_GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"

# Bump this together with the local weread-skills SKILL.md so the gateway
# knows we are speaking a compatible protocol version.
WEREAD_SKILL_VERSION = "1.0.4"

# Default key reads from env; can be overridden by callers.
DEFAULT_API_KEY = os.getenv("WEREAD_API_KEY")


class WeReadApi:
    def __init__(self, api_key: str = DEFAULT_API_KEY):
        if not api_key or not api_key.strip():
            raise Exception(
                "没有找到 WEREAD_API_KEY，请按文档申请并配置后再运行。"
                "申请地址：https://weread.qq.com/r/weread-skills"
            )
        self.api_key = api_key
        self.session = requests.Session()

    # ------------------------------------------------------------------
    # Low-level gateway request
    # ------------------------------------------------------------------
    def _request(self, api_name: str, **params):
        """Single call into the WeRead Agent API Gateway."""
        body = {
            "api_name": api_name,
            "skill_version": WEREAD_SKILL_VERSION,
            **params,
        }
        r = self.session.post(
            WEREAD_GATEWAY_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        try:
            data = r.json()
        except ValueError:
            raise Exception(
                f"微信读书 Gateway 返回非 JSON：{api_name}, "
                f"status={r.status_code}, body={r.text[:200]}"
            )

        errcode = data.get("errcode", 0) if isinstance(data, dict) else 0
        if errcode != 0:
            # Common auth errors are -2010 / -2012 (cookie expired) in the old
            # private API. The gateway uses different codes; surface them all.
            raise Exception(
                f"微信读书 Gateway 请求失败: {api_name}, "
                f"errcode={errcode}, response={data}"
            )
        return data

    # ------------------------------------------------------------------
    # High-level read APIs (signatures preserved for backward compatibility)
    # ------------------------------------------------------------------
    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_notebooklist(self):
        """获取笔记本列表。"""
        data = self._request("/user/notebooks", count=100)
        books = data.get("books")
        if books is None:
            return None
        books.sort(key=lambda x: x["sort"])
        return books

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_bookinfo(self, bookId):
        """获取书的详情。"""
        return self._request("/book/info", bookId=bookId)

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_bookmark_list(self, bookId):
        """获取我的划线。

        The legacy implementation also wrote the response to ``bookmark.json``
        on disk for debugging. We keep the same side effect to stay compatible
        with anything that depends on it.
        """
        data = self._request("/book/bookmarklist", bookId=bookId)
        import json

        with open("bookmark.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return data.get("updated")

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_read_info(self, bookId):
        """获取指定书的阅读时长/进度。

        官方 gateway 的 /book/getprogress 把几乎所有元数据塞在 ``book`` 嵌套
        对象里（readingTime / startReadingTime / finishTime / progress ...），
        而老 pro 版的私有 API 是平铺的（beginReadingDate / lastReadingDate
        / finishedDate / readingTime / readingProgress / markedStatus ...）。

        这里把 book 嵌套铺平 + 字段名映射到老格式，让 book.py 无需改动
        就能继续工作。
        """
        data = self._request("/book/getprogress", bookId=bookId)
        info = dict(data)  # shallow copy
        book = info.pop("book", None) or {}
        info.update(book)  # 铺平 book 嵌套

        # 字段名映射：新 API → 老 API 字段
        # 老代码用 pendulum.from_timestamp() 读这些字段，全是 Unix 时间戳
        if "startReadingTime" in info and info["startReadingTime"]:
            info["beginReadingDate"] = info["startReadingTime"]
        if "updateTime" in info and info["updateTime"]:
            info["lastReadingDate"] = info["updateTime"]
        if "finishTime" in info and info["finishTime"]:
            info["finishedDate"] = info["finishTime"]
        # 老代码用 readingProgress 是 0-100 的整数
        if "progress" in info and info["progress"] is not None:
            info["readingProgress"] = int(info["progress"])
        # 状态：finishTime 有值=已读(4)，否则看 isStartReading
        if "finishTime" in info and info["finishTime"]:
            info["markedStatus"] = 4
        elif "isStartReading" in info and info["isStartReading"]:
            info["markedStatus"] = 1  # 想读/在读
        # 保留 readingTime 同名字段（直接来自 book 嵌套）
        # recordReadingTime 是新字段，老代码用不上

        return info

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_review_list(self, bookId):
        """获取我的想法/点评。

        The legacy code did POST /review/list with a body, but the gateway
        version expects a flat body. We keep behavior equivalent: listType=11,
        mine=1, syncKey=0, plus a virtual "点评" chapter (chapterUid=1000000)
        for type=4 (book-level reviews) so they group under "点评" downstream.
        """
        data = self._request(
            "/review/list/mine",
            bookid=bookId,
            syncKey=0,
        )
        reviews = data.get("reviews") or []
        reviews = list(map(lambda x: x.get("review"), reviews))
        reviews = [
            {"chapterUid": 1000000, **x} if x.get("type") == 4 else x
            for x in reviews
        ]
        return reviews

    @retry(stop_max_attempt_number=3, wait_fixed=5000)
    def get_chapter_info(self, bookId):
        """获取书的章节信息。

        Returns a dict keyed by chapterUid, matching the legacy contract.
        Adds a synthetic "点评" chapter (chapterUid=1000000) so reviews with
        no chapter mapping still show up in the right place.

        The gateway returns ``{"chapters": [...]}`` at the top level; the
        legacy private API wrapped it as ``{"data": [{"updated": [...]}]}``.
        We accept both shapes to be safe.
        """
        data = self._request("/book/chapterinfo", bookId=bookId)
        chapters = None
        if isinstance(data, dict):
            if "chapters" in data and isinstance(data["chapters"], list):
                chapters = data["chapters"]
            elif (
                "data" in data
                and isinstance(data["data"], list)
                and len(data["data"]) >= 1
                and isinstance(data["data"][0], dict)
                and "updated" in data["data"][0]
            ):
                chapters = data["data"][0]["updated"]

        if not chapters:
            # 网文 (MP_WXS_*) 等可能没有章节结构；返回空 dict 让调用方继续
            return {}

        chapters.append(
            {
                "chapterUid": 1000000,
                "chapterIdx": 1000000,
                "updateTime": 1683825006,
                "readAhead": 0,
                "title": "点评",
                "level": 1,
            }
        )
        return {item["chapterUid"]: item for item in chapters}

    def get_api_data(self):
        """获取阅读时长历史（按月分桶的秒数）。

        ``read_time.py`` consumes ``api_data.get("readTimes")`` as a flat
        ``timestamp -> seconds`` map. The legacy private API
        ``/readdata/summary`` returned day-level buckets; the official
        gateway's ``/readdata/detail`` does not expose per-day data — only
        monthly buckets (``mode="annually"`` returns ~12 month entries,
        ``mode="monthly"`` returns 1 month entry).

        We project the current year's monthly buckets into the legacy shape
        so downstream code still works. The "day" Notion database will
        therefore only have data on the 1st of each month; the rest will be
        0. The month/week/year databases populate correctly.
        """
        from datetime import datetime

        now = datetime.utcnow()
        merged = {}
        # 拿本年（annually 模式按月分桶，12 条）
        base = int(datetime(now.year, 1, 1).timestamp())
        try:
            data = self._request(
                "/readdata/detail", mode="annually", baseTime=base
            )
            for k, v in (data.get("readTimes") or {}).items():
                merged[int(k)] = int(v)
        except Exception as e:
            print(f"warning: get_api_data {now.year} annually failed: {e}")

        # 当天补一条 0，让 read_time.py 把今天也写一行（不会破坏月度数据）
        today_ts = int(
            datetime(now.year, now.month, now.day).timestamp()
        )
        merged.setdefault(today_ts, 0)

        return {"readTimes": {str(k): v for k, v in merged.items()}}

    def get_bookshelf(self):
        """获取书架。"""
        return self._request("/shelf/sync", synckey=0, teenmode=0, album=1, onlyBookid=0)

    # ------------------------------------------------------------------
    # Pure helpers (no network), kept verbatim from the original.
    # ------------------------------------------------------------------
    def transform_id(self, book_id):
        id_length = len(book_id)
        if re.match(r"^\d*$", book_id):
            ary = []
            for i in range(0, id_length, 9):
                ary.append(format(int(book_id[i : min(i + 9, id_length)]), "x"))
            return "3", ary

        result = ""
        for i in range(id_length):
            result += format(ord(book_id[i]), "x")
        return "4", [result]

    def calculate_book_str_id(self, book_id):
        import hashlib

        md5 = hashlib.md5()
        md5.update(book_id.encode("utf-8"))
        digest = md5.hexdigest()
        result = digest[0:3]
        code, transformed_ids = self.transform_id(book_id)
        result += code + "2" + digest[-2:]

        for i in range(len(transformed_ids)):
            hex_length_str = format(len(transformed_ids[i]), "x")
            if len(hex_length_str) == 1:
                hex_length_str = "0" + hex_length_str

            result += hex_length_str + transformed_ids[i]

            if i < len(transformed_ids) - 1:
                result += "g"

        if len(result) < 20:
            result += digest[0 : 20 - len(result)]

        md5 = hashlib.md5()
        md5.update(result.encode("utf-8"))
        result += md5.hexdigest()[0:3]
        return result

    def get_url(self, book_id):
        return f"https://weread.qq.com/web/reader/{self.calculate_book_str_id(book_id)}"
