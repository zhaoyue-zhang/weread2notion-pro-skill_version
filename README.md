# weread2notion-pro · API Key 版

这是 [`malinkang/weread2notion-pro`](https://github.com/malinkang/weread2notion-pro) 的复活 fork。

**原版在 2024 年停更了**——它依赖直接打微信读书私有 API，腾讯加了风控，Cookie 经常失效。作者在原 README 写明"目前项目已不能使用"。

**这个 fork 做了什么**：把 `weread_api.py` 整层重写，从"模拟浏览器 + 私有 API"切换到**微信读书官方 Agent API Gateway** + API Key 鉴权，并在此基础上**加了完整 Notion schema、热力图、聚合、5 年回填等一整套工程化能力**。

---

## 跟原版 / 原开源版的区别

| 维度 | 原 `weread2notion-pro`（已挂） | 原 `weread2notion`（开源） | **这个 fork** |
|---|---|---|---|
| 鉴权 | 微信读书 Cookie | WEREAD_API_KEY | WEREAD_API_KEY |
| 数据源 | 私有 API（已废） | 官方 gateway | 官方 gateway |
| Notion 端 | 11 database + 增量更新 | 1 database + 全量覆盖 | 11 database + 增量更新 |
| 阅读热力图 | ✅ | ❌ | ✅（含标题/统计/图例/今日高亮） |
| 一键建库 | ❌（要手搭） | ❌ | ✅ `scripts/setup_databases.py` |
| 阅读时长显示 | 数字 | 数字 | "1h 23m" / "46d 14h" 友好格式 |
| 5 年历史回填 | ❌ | ❌ | ✅ `BACKFILL_YEARS=2021,...` |
| day pages 去重 | ❌ | ❌ | ✅ `scripts/dedupe_day_pages.py` |
| 用户手写批注 | 不会被覆盖 | 会被覆盖 | 不会被覆盖 |
| 活跃度 | 长期停更 | 仍在维护 | 持续维护 |

---

## 怎么用

### 1. 申请 WEREAD_API_KEY

打开 `https://weread.qq.com/r/weread-skills` → 微信扫码登录 → 复制页面里以 `wrk-` 开头的 key。**只显示一次，关掉就没了，请先存好。**

### 2. 在 Notion 里搭好 12 个 database（**新增：一键脚本**）

**最简方式**：跑 `scripts/setup_databases.py`，它会**自动建好 11 个 database**（还有一个「阅读记录」+「设置」由代码首次同步时自动建）。不用手搭。

```bash
export NOTION_TOKEN="secret_你的token"
export NOTION_PAGE="你的Notion父页面URL"
python3 scripts/setup_databases.py
```

它建的是这 12 个（默认 database 名，可在 `setup_databases.py` 改）：

| 默认名 | 用途 | 字段要点 |
|---|---|---|
| `书架` | 书籍主表 | 关联 分类 / 作者 / 年 / 月 / 周 / 日 |
| `划线` | 划线 | 关联 书架 + 时间维度 |
| `笔记` | 想法/点评 | 关联 书架 + 时间维度 |
| `章节` | 章节 | 关联 书架 |
| `作者` | 作者（独立 entity） | |
| `分类` | 分类（独立 entity） | |
| `年` | 年度阅读聚合 | 总阅读时长（自动渲染 "Xd Yh"） |
| `月` | 月度阅读聚合 | 总阅读时长（自动渲染 "Xh Ym"） |
| `周` | 周度阅读聚合 | 总阅读时长（自动渲染 "Xh Ym"） |
| `日` | 每日阅读时长 | 时长 + 时间戳 + 时间维度 relation |

⚠️ **如果你已经在用老 pro 版，11 个库已经存在**，setup 脚本会跳过。**总阅读时长自动格式**给老库加一下：

```bash
python3 scripts/migrate_to_duration.py
```

幂等，跑 10 次也没事。

### 3. Fork 仓库并配 GitHub Secrets

Fork 这个仓库到你自己的 GitHub → 进 `Settings` → `Secrets and variables` → `Actions` → 新增：

| Secret 名 | 是否必填 | 值 |
|---|---|---|
| `NOTION_TOKEN` | 必填 | Notion integration token（`secret_` 开头） |
| `NOTION_PAGE` | 必填 | 你的 Notion 父页面 URL |
| `WEREAD_API_KEY` | 必填 | 第 1 步拿到的 `wrk-` key |
| `HEATMAP_BLOCK_ID` | ❌ 已废弃 | embed block 改用 auto-detect |

> ⚠️ 旧的 `WEREAD_COOKIE` / `CC_URL` / `CC_ID` / `CC_PASSWORD` 全部不再需要。如果 fork 之前用过，**删掉**它们。

### 4. 手动跑一次

进仓库的 `Actions` 页面 → 选 `weread note sync` → `Run workflow` → 看日志。

- 绿色 ✅ = 成功，去 Notion 看效果
- 红色 ❌ = 翻日志最后几行的 `errcode=` 看错误

**两个 workflow 各司其职**：

- `weread note sync`（每 2 小时）：同步书、划线、笔记、章节、分类、作者
- `read time sync`（每 3 小时）：同步阅读时长 + 渲染热力图 + 推送到 GitHub + dedupe + aggregate

---

## 本地试运行

```bash
git clone <this fork>
cd weread2notion-pro-fork
pip install -r requirements.txt

export WEREAD_API_KEY="wrk-你的key"
export NOTION_TOKEN="secret_你的token"
export NOTION_PAGE="你的Notion页面URL"

# 验证 API Key 能不能用
python3 scripts/dry_run.py

# 一次性回填 5 年阅读时长（首次部署用，平时不用跑）
BACKFILL_YEARS=2021,2022,2023,2024,2025 python3 scripts/backfill_historical_days.py

# 一次性回填 256 本书的分类（如果分类是空的）
python3 scripts/backfill_categories.py

# 同步书
book

# 同步划线/笔记
weread

# 同步阅读时长
read_time

# 清理「日」database 重复 day pages
python3 scripts/dedupe_day_pages.py

# 把日时长 roll-up 到 年/月/周
python3 scripts/aggregate_durations.py
```

---

## 阅读热力图

`scripts/build_heatmap.py` 渲染一个 SVG，**已 push 到 `OUT_FOLDER/weread-heatmap.svg`**，**Notion embed block 自动检测抓取**（不用配 `HEATMAP_BLOCK_ID` secret）。

SVG 长这样：

- 大标题：`<昵称> 的阅读记录`
- 副标题：`<年份>：总阅读时长 X 小时 Y 分钟 · 阅读 X 天 · 最长全勤 X 天`
- 5 档颜色：0 / <30 分 / 30-60 分 / 1-2 小时 / 2 小时+
- **今天的格子描边高亮**
- 底部颜色图例（少 / 多）

> 早期版本会显示 "鼠标悬停查看 X 分钟"——Notion embed 不支持 hover，**已移除**，避免误导。

**Notion embed 缓存问题**：Notion 第一次抓 SVG 后会缓存，不会重新拉 raw.githubusercontent.com 的新版本。代码用 `?v=<utc_timestamp>` cache-busting，每次发布换 URL 强制 Notion 重新抓。

---

## 「总阅读时长」显示为 "1h 23m"

Notion API **目前不支持 `duration` 字段类型**（虽然 UI 里有）。我用 number + formula 组合实现：

- number 字段「总阅读时长」：存**分钟整数**
- formula 字段「总阅读时长（格式化）」：自动渲染

公式（年/月/周 database 一致）：

```
if(empty(prop("总阅读时长")), "",
if(prop("总阅读时长") == 0, "",
if(prop("总阅读时长") >= 1440,
   format(floor(prop("总阅读时长")/1440)) + "d " + format(floor(mod(prop("总阅读时长"),1440)/60)) + "h",
if(prop("总阅读时长") >= 60,
   format(floor(prop("总阅读时长")/60)) + "h " + format(mod(prop("总阅读时长"),60)) + "m",
format(prop("总阅读时长")) + "m"))))
```

效果：2024 年 = "46d 14h"，2025 年第 52 周 = "15h 10m"，2026年8月 = "3m"。

**Notion UI 建议**：把「总阅读时长（格式化）」formula 列拉到显眼位置，把 number 列右键 Hide。

---

## 5 年阅读时长回填

`BACKFILL_YEARS=2021,2022,2023,2024,2025` 一次性把 5 年 day pages 写进「日」database。

**关键发现**：官方 `/readdata/detail` 端点用 `mode=monthly` + `baseTime=上海 0 时区 timestamp` 返回该月真实 daily 粒度数据，**接受历史年的 baseTime**，不是 fake 均摊。

旧实现用 `mode=annually` 只返回 12 个月分桶（每月值是聚合好的 daily），key 用 UTC 0 时区月 1 号，导致**同一个日历日产生 2 个 day page**（差 8 小时）。

回填完跑 `scripts/dedupe_day_pages.py` 清掉重复，最后 `scripts/aggregate_durations.py` roll-up 到年/月/周。

---

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `没有找到 WEREAD_API_KEY` | Secret 没填或填错 | 检查 Secrets 页 |
| Gateway `errcode != 0` | API Key 失效 | 重新到 `https://weread.qq.com/r/weread-skills` 申请 |
| Notion 404 | `NOTION_PAGE` 错 | 用完整 URL |
| Notion 401 | integration 没被授权到页面 | 在 Notion 页面加 connection |
| 同步跑完 Notion 没数据 | 12 个 database 缺一个 | 检查页面里 `书架` 等库是否存在；或跑 `setup_databases.py` |
| 章节全堆一起 | `CHAPTER_DATABASE_NAME` 找不到 | 同上 |
| 书的「分类」是空的 | 官方 `/book/info` 改成了单数 `category` 字符串（"精品小说-社会小说"） | 跑 `scripts/backfill_categories.py` |
| 同一天有 2 个 day page | 时区差异（UTC 0 vs 上海 0） | 跑 `scripts/dedupe_day_pages.py` |
| 嵌入的热力图不更新 | Notion embed 缓存 | 确认代码有 `?v=<timestamp>` cache-busting；Notion 第一次抓后不会自动刷新 |
| workflow 偶发失败 | Notion API 瞬时 5xx | dedupe + aggregate 已加固，失败会 continue 不会让 workflow 挂；「日」database 原数据已写入，下次跑能修 |
| 找不到 `年` database | user 改名为 `阅读数据` | notion_helper 内置 `RENAMED` fallback 自动兼容 |

---

## 跟原版代码的差异

### 整层重写
- `weread2notionpro/weread_api.py` — 从私有 API 切到官方 Agent API Gateway；`get_daily_data(years=...)` 支持 5 年回填

### 新增 scripts
- `scripts/setup_databases.py` — 一键建 12 个 database（含 relations 接线）
- `scripts/dry_run.py` — 验证 API Key + Notion 鉴权
- `scripts/build_heatmap.py` — 渲染带标题/统计/图例/今日高亮的 SVG
- `scripts/backfill_historical_days.py` — 一次性回填 5 年 day pages（`BACKFILL_YEARS=2021,...`）
- `scripts/backfill_categories.py` — 把 256 本书的「分类」relation 补全
- `scripts/dedupe_day_pages.py` — 清理同日重复 day page
- `scripts/aggregate_durations.py` — 从「日」database 聚合到年/月/周
- `scripts/migrate_to_duration.py` — 给已有库补「总阅读时长（格式化）」formula 字段

### 改动的核心文件
- `weread2notionpro/book.py` — 读官方 `/book/info` 的 `category` 单数字符串（"精品小说-社会小说"），按 `- · / >` 拆多级
- `weread2notionpro/notion_helper.py` — embed block 改用 auto-detect（扫页面找 `raw.githubusercontent.com/.../weread-heatmap.svg`），不再依赖 `HEATMAP_BLOCK_ID` secret；加 `RENAMED` fallback 兼容「年」→「阅读数据」改名
- `weread2notionpro/read_time.py` — Notion embed URL 加 `?v=<utc_timestamp>` cache-busting；用 `glob` 找最新 svg（兼容 UUID 改名）
- `.github/workflows/weread.yml` — secrets 改 `WEREAD_API_KEY`，push 用 `git add -f`
- `.github/workflows/read_time.yml` — secrets 改 `WEREAD_API_KEY`；dedupe + aggregate 步骤加 `continue-on-error: true`（best-effort）
- `.gitignore` — 不再忽略 `OUT_FOLDER/`（曾经让 SVG 在 raw.githubusercontent.com 404）

### 一字未改
- `weread2notionpro/weread.py`
- `weread2notionpro/utils.py`
- `weread2notionpro/config.py`
- `weread2notionpro/__main__.py`

---

## License

MIT（同原版）。原版权属原作者 malinkang。
