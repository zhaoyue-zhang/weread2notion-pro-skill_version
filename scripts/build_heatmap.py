"""Build a WeRead heatmap using the official Agent API Gateway.

Reuses github_heatmap's full pipeline (Loader → Poster → Drawer) by
monkey-patching WereadLoader's data source to use the official gateway
+ WEREAD_API_KEY instead of the dead WeRead cookie path. Then we
post-process the generated SVG to inject a richer title block (year
+ total reading time / active days / longest streak) so the Notion
embed is self-explanatory.

Output: writes OUT_FOLDER/weread.svg (matching the read_time.yml
convention). The svgwrite output is then mutated to:
  - swap the title text from "wechat-read" → "zhangyue 的阅读记录"
  - add a 2nd line with "2026：总阅读时长 X 小时 Y 分钟 · 阅读 X 天 · 最长连续 X 天"
  - grow the viewBox so the extra line fits without overlapping cells
"""
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# Add repo root to path so we can import the installed github_heatmap
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WEREAD_GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.4"
USER_DISPLAY_NAME = os.getenv("WEREAD_DISPLAY_NAME", "zhangyue")


def _gateway_request(api_key, api_name, **params):
    body = {"api_name": api_name, "skill_version": SKILL_VERSION, **params}
    r = requests.post(
        WEREAD_GATEWAY_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=15,  # 原来 30s 太长：12 个月 × 30s = 6 分钟；CI 上卡 5+ 分钟常态
    )
    r.raise_for_status()
    data = r.json()
    if data.get("errcode", 0) != 0:
        raise Exception(
            f"Gateway error: {api_name} errcode={data.get('errcode')}"
        )
    return data


def fetch_daily_read_times(api_key, year):
    """Loop 12 months of mode=monthly, merge readTimes, return
    ``{timestamp_int: seconds}`` shape for the year (Asia/Shanghai
    day boundaries)."""
    merged = {}
    tz = ZoneInfo("Asia/Shanghai")
    for month in range(1, 13):
        try:
            base = int(datetime(year, month, 1, tzinfo=tz).timestamp())
        except ValueError:
            continue
        try:
            data = _gateway_request(
                api_key, "/readdata/detail", mode="monthly", baseTime=base
            )
        except Exception as e:
            print(f"warn: {year}-{month:02d} fetch failed: {e}")
            continue
        for k, v in (data.get("readTimes") or {}).items():
            merged[int(k)] = int(v)
        # 礼貌避开 rate limit（之前连续 12 次偶发 throttle）
        time.sleep(0.3)
    return merged


def compute_stats(read_times_for_year: dict, year: int) -> dict:
    """Return dict with total_seconds / hours_minutes / active_days / longest_streak."""
    tz = ZoneInfo("Asia/Shanghai")
    # Filter to this year (Asia/Shanghai day boundary), keep only positive days
    year_days = {}
    for ts, secs in read_times_for_year.items():
        dt = datetime.fromtimestamp(int(ts), tz=tz)
        if dt.year != year or secs <= 0:
            continue
        day_key = int(datetime(dt.year, dt.month, dt.day, tzinfo=tz).timestamp())
        year_days[day_key] = year_days.get(day_key, 0) + secs

    total_seconds = sum(year_days.values())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    active_days = len(year_days)

    # Longest consecutive streak of days (only counting days up to today)
    today_ts = int(datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    longest = 0
    cur = 0
    for ts in sorted(year_days.keys()):
        if ts > today_ts:
            break
        if year_days[ts] > 0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return {
        "total_seconds": total_seconds,
        "hours": int(hours),
        "minutes": int(minutes),
        "active_days": active_days,
        "longest_streak": longest,
    }


def make_patched_loader(api_key, cache=None):
    """Return a WereadLoader subclass whose data source is the gateway.

    If ``cache`` is provided, ``get_api_data`` reads/writes through it
    so multiple Loader instances (or the explicit stats call after
    run()) share the same data without re-fetching.
    """
    from github_heatmap.loader.weread_loader import WereadLoader

    class GatewayWereadLoader(WereadLoader):
        def refresh_token(self):
            pass  # no notionhub involvement

        def get_api_data(self):
            if cache is not None:
                return cache.setdefault(
                    "data",
                    {"readTimes": fetch_daily_read_times(api_key, self.from_year)},
                )
            return {"readTimes": fetch_daily_read_times(api_key, self.from_year)}

    return GatewayWereadLoader


def inject_title(svg_path: str, year: int, stats: dict) -> None:
    """Rewrite the SVG header to show:
       Line 1: {USER_DISPLAY_NAME} 的阅读记录
       Line 2: {year}：总阅读时长 {h} 小时 {m} 分钟 · 阅读 {d} 天 · 最长全勤 {s} 天
    and grow the viewBox to fit the extra line. Also:
      - removes the redundant "2026: 128 hours" line that
        github_heatmap's drawer auto-adds in the per-year summary
      - adds a color legend (4 swatches + 少/多 labels) below the stats
      - highlights "today" with a 2px outline so the current cell stands out
    """
    with open(svg_path, "r", encoding="utf-8") as f:
        svg = f.read()

    title_line = f"{USER_DISPLAY_NAME} 的阅读记录"
    stats_line = (
        f"{year}：总阅读时长 {stats['hours']} 小时 {stats['minutes']} 分钟"
        f" · 阅读 {stats['active_days']} 天"
        f" · 最长全勤 {stats['longest_streak']} 天"
    )

    # GitHub-style 4-level color scale (matches the colors github_heatmap
    # uses by default). We hardcode them here because the monkey-patched
    # loader doesn't expose p.level_colors cleanly. Note: labels containing
    # '<' must be XML-escaped (&lt;) since we hand-stitch them into the
    # SVG text content.
    LEGEND = [
        ("#EBEDF0", "0"),
        ("#ACE7AE", "&lt;30 分"),
        ("#69C16E", "30-60 分"),
        ("#549F57", "1-2 小时"),
        ("#2F7D32", "2 小时+"),
    ]

    # 1) Grow viewBox/width/height. We need ~10mm extra this time (stats
    #    line + color legend row + 留 padding). Target the <svg ...> opening
    #    tag specifically to avoid matching the "<?xml ?>" prolog's '>'.
    extra_mm = 10.0

    def bump_svg_root_attr(attr, value_kind=""):
        nonlocal svg
        svg = re.sub(
            rf'(<svg\b[^>]*?){attr}="([\d.]+)({value_kind})"',
            lambda m: (
                f'{m.group(1)}{attr}="{float(m.group(2)) + extra_mm}{m.group(3)}"'
            ),
            svg,
            count=1,
        )

    bump_svg_root_attr("height", "mm")
    bump_svg_root_attr("width", "mm")
    svg = re.sub(
        r'(<svg\b[^>]*?)viewBox="0,0,([\d.]+),([\d.]+)"',
        lambda m: (
            f'{m.group(1)}viewBox="0,0,'
            f'{float(m.group(2)) + extra_mm},'
            f'{float(m.group(3)) + extra_mm}"'
        ),
        svg,
        count=1,
    )

    # Bump the background <rect> too
    svg = re.sub(
        r'(<rect fill="#FFFFFF" height=")([\d.]+)("\s+width=")([\d.]+)("\s+x="0"\s+y="0"\s*/>)',
        lambda m: (
            f'{m.group(1)}{float(m.group(2)) + extra_mm}{m.group(3)}'
            f'{float(m.group(4)) + extra_mm}{m.group(5)}'
        ),
        svg,
        count=1,
    )

    # 2) Delete the redundant "2026: 128 hours" line (drawer auto-added)
    svg = re.sub(
        r'<text[^>]*font-size:3px[^>]*>[^<]*hours</text>',
        "",
        svg,
        count=1,
    )

    # 3) Replace the original wechat-read title text with the new title + stats line
    title_pattern = re.compile(
        r'(<text fill="#000000" style="font-size:6px;[^"]*"[^>]*>)wechat-read(</text>)'
    )
    new_title_block = (
        f'<text fill="#000000" style="font-size:6px; font-family:Arial; font-weight:bold;" x="10" y="10">'
        f'{title_line}</text>'
        f'<text fill="#000000" style="font-size:3.5px; font-family:Arial;" x="10" y="15.5">'
        f'{stats_line}</text>'
    )
    svg, n = title_pattern.subn(new_title_block, svg, count=1)
    if n == 0:
        print("warn: could not find original wechat-read title to replace")

    # 4) Append a color legend (swatches + 少/多 + duration labels) right
    #    before the closing </svg>. Sizes are tuned so the legend stays
    #    readable at typical Notion-embed widths (~700-1000px).
    swatch_size = 5.0
    label_gap = 2.0
    legend_y = 66.5  # mm — below the heatmap (heatmap ends at ~62.5mm)
    cur_x = 10.0
    swatches_xml = []
    for fill, label in LEGEND:
        swatches_xml.append(
            f'<rect fill="{fill}" x="{cur_x}" y="{legend_y}" '
            f'width="{swatch_size}" height="{swatch_size}" rx="1" ry="1" />'
        )
        swatches_xml.append(
            f'<text fill="#000000" style="font-size:3.5px; font-family:Arial;" '
            f'x="{cur_x}" y="{legend_y + swatch_size + 3.8}">{label}</text>'
        )
        cur_x += swatch_size + label_gap
    # "少" / "多" labels at the ends
    cur_x += 1.0
    swatches_xml.append(
        f'<text fill="#000000" style="font-size:4px; font-family:Arial; font-weight:bold;" '
        f'x="{cur_x}" y="{legend_y + swatch_size - 0.8}">少</text>'
    )
    cur_x += 5.0
    swatches_xml.append(
        f'<text fill="#000000" style="font-size:4px; font-family:Arial; font-weight:bold;" '
        f'x="{cur_x}" y="{legend_y + swatch_size - 0.8}">多</text>'
    )

    # Caption above the legend (italic, gray). Note: Notion embed renders
    # SVG as a static image — the <title> tooltips that github_heatmap
    # adds on each day-cell are not interactive there, so we don't promise
    # hover-to-inspect in the caption. Anyone who wants per-day detail
    # can open the SVG URL directly in a browser.
    caption = (
        f'<text fill="#666666" style="font-size:3.2px; font-family:Arial; font-style:italic;" '
        f'x="10" y="{legend_y - 1.2}">颜色越深代表当天阅读时长越长</text>'
    )

    legend_block = caption + "".join(swatches_xml)
    svg = svg.replace("</svg>", legend_block + "</svg>")

    # 5) Highlight "today" with a 2px black stroke so the current cell
    #    stands out (matches CarveTime's is-today outline).
    tz = ZoneInfo("Asia/Shanghai")
    today_str = datetime.now(tz).strftime("%Y-%m-%d")
    # Find the <rect ...><title>YYYY-MM-DD</title></rect> for today and
    # add a stroke="..." stroke-width="0.4" to the rect.
    pattern = re.compile(
        rf'(<rect[^/]*?)(>\s*<title>{today_str}</title>)',
    )
    if pattern.search(svg):
        svg, n = pattern.subn(
            r'\1 stroke="#000000" stroke-width="0.4"\2',
            svg,
            count=1,
        )
        if n:
            print(f"highlighted today ({today_str})")
    else:
        print(f"warn: today ({today_str}) not in heatmap (skipping outline)")

    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"injected title + stats + legend into {svg_path}")


def main():
    api_key = os.getenv("WEREAD_API_KEY")
    if not api_key:
        print("ERROR: WEREAD_API_KEY not set")
        sys.exit(1)

    year = int(os.getenv("YEAR") or datetime.now().year)

    out_folder = os.path.join(ROOT, "OUT_FOLDER")
    os.makedirs(out_folder, exist_ok=True)

    from github_heatmap.cli import LOADER_DICT
    LOADER_DICT["weread"] = make_patched_loader(api_key)

    bg = os.getenv("background_color", "#FFFFFF")
    track = os.getenv("track_color", "#ACE7AE")
    sp1 = os.getenv("special_color", "#69C16E")
    sp2 = os.getenv("special_color2", "#549F57")
    dom = os.getenv("dom_color", "#EBEDF0")
    txt = os.getenv("text_color", "#000000")

    sys.argv = [
        "github_heatmap",
        "weread",
        "--year", str(year),
        "--me", "wechat-read",  # placeholder, replaced by inject_title
        "--without-type-name",
        "--background-color", bg,
        "--track-color", track,
        "--special-color1", sp1,
        "--special-color2", sp2,
        "--dom-color", dom,
        "--text-color", txt,
    ]

    from github_heatmap.cli import run
    # 共享 cache：cli.run() 里 Loader.get_api_data() 和我们后面 compute_stats
    # 用的都是同一份 readTimes，节省一半 API 调用（之前是 12+12=24 次）
    _cache = {}
    LOADER_DICT["weread"] = make_patched_loader(api_key, cache=_cache)
    run()

    out_file = os.path.join(ROOT, "OUT_FOLDER", "weread.svg")
    if not os.path.exists(out_file):
        print("ERROR: cli.run did not produce weread.svg")
        sys.exit(1)

    # 复用 cache，不再 fetch 第二次
    read_times = _cache.get("data", {}).get("readTimes", {})
    stats = compute_stats(read_times, year)
    print(f"stats for {year}: {stats}")

    inject_title(out_file, year, stats)

    # Force every build to produce a different file (otherwise git
    # sees no diff when the heatmap data is identical day-over-day
    # and "nothing to commit" makes the workflow silently fail).
    with open(out_file, "a", encoding="utf-8") as f:
        f.write(f"\n<!-- built at {datetime.utcnow().isoformat()}Z -->\n")
    print(f"appended timestamp to {out_file}")


if __name__ == "__main__":
    main()
