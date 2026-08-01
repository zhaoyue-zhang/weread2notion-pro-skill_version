"""Build a GitHub-style WeRead heatmap using the official Agent API Gateway.

Reuses github_heatmap's full pipeline (Loader → Poster → Drawer) by
monkey-patching WereadLoader's data source to use the official gateway
+ WEREAD_API_KEY instead of the dead WeRead cookie path. This way we get
the same GitHub-poster styling without depending on the NotionHub
commercial service.

Output: writes OUT_FOLDER/weread.svg (matching the read_time.yml
convention).
"""
import os
import sys
from datetime import datetime

import requests

# Add repo root to path so we can import the installed github_heatmap
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WEREAD_GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.4"


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
    """Loop 12 months of mode=monthly, merge dailyReadTimes, return
    ``{timestamp_str: seconds}`` (matches legacy ``/readdata/summary`` shape)."""
    merged = {}
    for month in range(1, 13):
        try:
            base = int(datetime(year, month, 1).timestamp())
        except ValueError:
            continue
        try:
            data = _gateway_request(api_key, "/readdata/detail",
                                   mode="monthly", baseTime=base)
        except Exception as e:
            print(f"warn: {year}-{month:02d} fetch failed: {e}")
            continue
        # monthly 模式下 readTimes 本身就是按日分桶
        for k, v in (data.get("readTimes") or {}).items():
            merged[int(k)] = int(v)
    return {str(k): v for k, v in merged.items()}


def make_patched_loader(api_key):
    """Return a WereadLoader subclass whose data source is the gateway."""
    from github_heatmap.loader.weread_loader import WereadLoader

    class GatewayWereadLoader(WereadLoader):
        def refresh_token(self):
            pass  # no notionhub involvement

        def get_api_data(self):
            return {"readTimes": fetch_daily_read_times(api_key, self.from_year)}

    return GatewayWereadLoader


def main():
    api_key = os.getenv("WEREAD_API_KEY")
    if not api_key:
        print("ERROR: WEREAD_API_KEY not set")
        sys.exit(1)

    year = int(os.getenv("YEAR") or datetime.now().year)

    # Make sure OUT_FOLDER exists; cli.run writes to current dir
    out_folder = os.path.join(ROOT, "OUT_FOLDER")
    os.makedirs(out_folder, exist_ok=True)
    # Note: we don't chdir — OUT_FOLDER is a relative path cli.run uses

    # Replace the registered WereadLoader in github_heatmap's LOADER_DICT
    # so cli.run picks up our gateway-backed version.
    from github_heatmap.cli import LOADER_DICT
    LOADER_DICT["weread"] = make_patched_loader(api_key)

    # Color/args come from env (with sensible defaults)
    bg = os.getenv("background_color", "#FFFFFF")
    track = os.getenv("track_color", "#ACE7AE")
    sp1 = os.getenv("special_color", "#69C16E")
    sp2 = os.getenv("special_color2", "#549F57")
    dom = os.getenv("dom_color", "#EBEDF0")
    txt = os.getenv("text_color", "#000000")

    # cli.run uses argparse.parse_args() which reads sys.argv
    sys.argv = [
        "github_heatmap",
        "weread",
        "--year", str(year),
        "--me", "wechat-read",
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


if __name__ == "__main__":
    main()
