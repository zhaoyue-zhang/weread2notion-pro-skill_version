# weread2notion-pro · API Key 版

这是 [`malinkang/weread2notion-pro`](https://github.com/malinkang/weread2notion-pro) 的复活 fork。

**原版在 2024 年停更了**——它依赖直接打微信读书私有 API，腾讯加了风控，Cookie 经常失效。作者在原 README 写明"目前项目已不能使用"。

**这个 fork 做了什么**：把 `weread_api.py` 整层重写，从"模拟浏览器 + 私有 API"切换到**微信读书官方 Agent API Gateway** + API Key 鉴权。其他文件（`weread.py` / `book.py` / `read_time.py` / `notion_helper.py` / `utils.py`）**一字未改**，所以原版所有的"按 bookmarkId 增量更新""阅读热力图""多 database 关系视图"全部保留。

## 跟原版 / 原开源版的区别

| 维度 | 原 `weread2notion-pro`（已挂） | 原 `weread2notion`（开源） | **这个 fork** |
|---|---|---|---|
| 鉴权 | 微信读书 Cookie | WEREAD_API_KEY | WEREAD_API_KEY |
| 数据源 | 私有 API（已废） | 官方 gateway | 官方 gateway |
| Notion 端 | 11 database + 增量更新 | 1 database + 全量覆盖 | 11 database + 增量更新 |
| 阅读热力图 | ✅ | ❌ | ✅ |
| 用户手写批注 | 不会被覆盖 | 会被覆盖 | 不会被覆盖 |
| 活跃度 | 长期停更 | 仍在维护 | 看下面 |

## 怎么用

### 1. 申请 WEREAD_API_KEY

打开 `https://weread.qq.com/r/weread-skills` → 微信扫码登录 → 复制页面里以 `wrk-` 开头的 key。**只显示一次，关掉就没了，请先存好。**

### 2. 在 Notion 里搭好 11 个 database

原版的 `notion_helper.py` 期望 11 个 database 已存在：

| 变量 | 默认 database 名 | 用途 |
|---|---|---|
| `BOOK_DATABASE_NAME` | `书架` | 书籍主表 |
| `BOOKMARK_DATABASE_NAME` | `划线` | 划线（关联到书） |
| `REVIEW_DATABASE_NAME` | `笔记` | 想法/点评（关联到书） |
| `CHAPTER_DATABASE_NAME` | `章节` | 章节（关联到书） |
| `AUTHOR_DATABASE_NAME` | `作者` | 作者（独立 entity） |
| `CATEGORY_DATABASE_NAME` | `分类` | 分类（独立 entity） |
| `YEAR_DATABASE_NAME` | `年` | 年度阅读聚合 |
| `MONTH_DATABASE_NAME` | `月` | 月度阅读聚合 |
| `WEEK_DATABASE_NAME` | `周` | 周度阅读聚合 |
| `DAY_DATABASE_NAME` | `日` | 每日阅读时长 |

⚠️ **这是这个 fork 最重的一步**——原版假设你已经手动搭好这些库。最简单的做法：找原 pro 版作者的 Notion 模板（社区里有人分享），复制一份到自己 workspace，然后授权给 integration。

### 3. Fork 仓库并配 GitHub Secrets

Fork 这个仓库到你自己的 GitHub → 进 `Settings` → `Secrets and variables` → `Actions` → 新增：

| Secret 名 | 值 |
|---|---|
| `NOTION_TOKEN` | Notion integration token（`secret_` 开头） |
| `NOTION_PAGE` | 你的 Notion 父页面 URL |
| `WEREAD_API_KEY` | 第 1 步拿到的 `wrk-` key |
| `HEATMAP_BLOCK_ID` | （可选）阅读热力图要插入的 Notion block id |

> ⚠️ 旧的 `WEREAD_COOKIE` / `CC_URL` / `CC_ID` / `CC_PASSWORD` 全部不再需要。如果 fork 之前用过，**删掉**它们。

### 4. 手动跑一次

进仓库的 `Actions` 页面 → 选 `weread note sync` → `Run workflow` → 看日志。

- 绿色 ✅ = 成功，去 Notion 看效果
- 红色 ❌ = 翻日志最后几行的 `errcode=` 看错误

`weread note sync` 每 2 小时自动跑一次（cron `0 */2 * * *`），`read time sync` 每 3 小时一次（`0 */3 * * *`）。

## 本地试运行

```bash
git clone <this fork>
cd weread2notion-pro-fork
pip install -r requirements.txt

export WEREAD_API_KEY="wrk-你的key"
export NOTION_TOKEN="secret_你的token"
export NOTION_PAGE="你的Notion页面URL"

# 先 dry-run 验证 API Key 能不能用
python scripts/dry_run.py

# 同步书
book

# 同步划线/笔记
weread
```

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `没有找到 WEREAD_API_KEY` | Secret 没填或填错 | 检查 Secrets 页 |
| Gateway `errcode != 0` | API Key 失效 | 重新到 `https://weread.qq.com/r/weread-skills` 申请 |
| Notion 404 | `NOTION_PAGE` 错 | 用完整 URL |
| Notion 401 | integration 没被授权到页面 | 在 Notion 页面加 connection |
| 同步跑完 Notion 没数据 | 11 个 database 缺一个 | 检查页面里 `书架` 等库是否存在 |
| 章节全堆一起 | `CHAPTER_DATABASE_NAME` 找不到 | 同上 |

## 跟原版代码的差异

- `weread2notionpro/weread_api.py` —— **整层重写**
- `.github/workflows/weread.yml` —— 删了 cookie 相关 secrets，换成 `WEREAD_API_KEY`
- `.github/workflows/read_time.yml` —— 同上
- 其他文件**未改动**（`book.py` / `weread.py` / `read_time.py` / `notion_helper.py` / `utils.py` / `__main__.py` / `config.py`）

## License

MIT（同原版）。原版权属原作者 malinkang。
