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
        timeout=30,
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


def make_patched_loader(api_key):
    """Return a WereadLoader subclass whose data source is the gateway."""
    from github_heatmap.loader.weread_loader import WereadLoader

    class GatewayWereadLoader(WereadLoader):
        def refresh_token(self):
            pass  # no notionhub involvement

        def get_api_data(self):
            return {"readTimes": fetch_daily_read_times(api_key, self.from_year)}

    return GatewayWereadLoader


def inject_title(svg_path: str, year: int, stats: dict) -> None:
    """Rewrite the SVG header to show:
       Line 1: {USER_DISPLAY_NAME} 的阅读记录
       Line 2: {year}：总阅读时长 {h} 小时 {m} 分钟 · 阅读 {d} 天 · 最长连续 {s} 天
    and grow the viewBox to fit the extra line. Also removes the
    redundant "2026: 128 hours" line that github_heatmap's drawer
    auto-adds in the per-year summary."""
    with open(svg_path, "r", encoding="utf-8") as f:
        svg = f.read()

    title_line = f"{USER_DISPLAY_NAME} 的阅读记录"
    stats_line = (
        f"{year}：总阅读时长 {stats['hours']} 小时 {stats['minutes']} 分钟"
        f" · 阅读 {stats['active_days']} 天"
        f" · 最长连续 {stats['longest_streak']} 天"
    )

    # 1) Grow viewBox/width/height by ~5mm so the second line doesn't overlap cells.
    #    We target the <svg ...> opening tag specifically so we don't match the
    #    "<?xml ... ?>" prolog's '>' (which is the first '>' in the file).
    extra_mm = 5.0

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
    # viewBox has form "0,0,W,H" — bump W and H
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

    # 2) Bump the background <rect> width/height too
    svg = re.sub(
        r'(<rect fill="#FFFFFF" height=")([\d.]+)("\s+width=")([\d.]+)("\s+x="0"\s+y="0"\s*/>)',
        lambda m: (
            f'{m.group(1)}{float(m.group(2)) + extra_mm}{m.group(3)}'
            f'{float(m.group(4)) + extra_mm}{m.group(5)}'
        ),
        svg,
        count=1,
    )

    # 3) Delete the redundant "2026: 128 hours" line (drawer auto-added)
    svg = re.sub(
        r'<text[^>]*font-size:3px[^>]*>[^<]*hours</text>',
        "",
        svg,
        count=1,
    )

    # 4) Replace the original wechat-read title text with the new title + stats line
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
    else:
        print(f"injected title + stats into {svg_path}")

    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)


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
    run()

    out_file = os.path.join(ROOT, "OUT_FOLDER", "weread.svg")
    if not os.path.exists(out_file):
        print("ERROR: cli.run did not produce weread.svg")
        sys.exit(1)

    # Compute stats from the same data source the heatmap used
    read_times = fetch_daily_read_times(api_key, year)
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
