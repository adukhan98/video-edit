"""Delivery formats: canvas size, safe zone and caption band per platform.

One table shared by brand_kit.py (DESIGN.md), captions.py (default caption
position) and slots.py (safe-zone guides on the QA contact sheet), so the three
can never disagree about where content may go.

Coordinates are pixels on the output canvas, origin top-left.
  safe          (x0, y0, x1, y1)  every piece of text, logo and animated element stays inside
  caption_band  (y0, y1)          reserved for burned-in captions; graphics keep it quiet
  caption_y     y                 default caption centre line

Usage:
    python helpers/platforms.py            # print the table
"""

from __future__ import annotations

PLATFORMS: dict[str, dict] = {
    # Instagram Reels. Bottom ~440 px is caption/username/audio UI, right ~120 px is the
    # like/comment/share rail. Top 285 px: the IG feed shows Reels as a 4:5 centre crop
    # (y 285-1635), so anything above it disappears in the feed. Proven on a shipped reel.
    "reels": {
        "label": "Instagram Reels 9:16",
        "canvas": (1080, 1920),
        "safe": (60, 285, 960, 1480),
        "caption_band": (1250, 1480),
        "caption_y": 1360,
    },
    # TikTok: tabs ~160 px on top, caption + sound ~400 px at the bottom, rail ~140 px right.
    "tiktok": {
        "label": "TikTok 9:16",
        "canvas": (1080, 1920),
        "safe": (60, 160, 940, 1520),
        "caption_band": (1290, 1520),
        "caption_y": 1400,
    },
    # YouTube Shorts: title, channel and subscribe button ~360 px at the bottom, actions right.
    "shorts": {
        "label": "YouTube Shorts 9:16",
        "canvas": (1080, 1920),
        "safe": (60, 120, 960, 1560),
        "caption_band": (1330, 1560),
        "caption_y": 1440,
    },
    # Intersection of the three above: one vertical master that is safe everywhere.
    "vertical": {
        "label": "Vertical 9:16, safe on Reels + TikTok + Shorts",
        "canvas": (1080, 1920),
        "safe": (60, 285, 940, 1480),
        "caption_band": (1250, 1480),
        "caption_y": 1360,
    },
    # 16:9 title-safe (90%). YouTube end screens cover the last 5-20 s; keep CTAs clear of them.
    "youtube": {
        "label": "YouTube / landscape 16:9",
        "canvas": (1920, 1080),
        "safe": (96, 54, 1824, 1026),
        "caption_band": (860, 1000),
        "caption_y": 930,
    },
    "square": {
        "label": "Square 1:1 feed",
        "canvas": (1080, 1080),
        "safe": (54, 54, 1026, 1026),
        "caption_band": (800, 960),
        "caption_y": 880,
    },
    "feed45": {
        "label": "Portrait feed 4:5 (Instagram / Facebook / LinkedIn)",
        "canvas": (1080, 1350),
        "safe": (54, 54, 1026, 1296),
        "caption_band": (1080, 1250),
        "caption_y": 1165,
    },
}

DEFAULT_PLATFORM = "vertical"


def get_platform(name: str) -> dict:
    if name not in PLATFORMS:
        raise KeyError(f"unknown platform '{name}'. Known: {', '.join(PLATFORMS)}")
    return PLATFORMS[name]


def platform_for_canvas(width: int, height: int) -> str:
    """Best preset for a canvas size: exact match first, else by aspect ratio."""
    for name, p in PLATFORMS.items():
        if p["canvas"] == (width, height) and name in ("vertical", "youtube", "square", "feed45"):
            return name
    ratio = width / max(1, height)
    if ratio < 0.7:
        return "vertical"
    if ratio < 0.9:
        return "feed45"
    if ratio < 1.2:
        return "square"
    return "youtube"


def scaled(platform: dict, width: int, height: int) -> dict:
    """The preset's zones scaled onto a canvas of another size (e.g. 720p drafts)."""
    cw, ch = platform["canvas"]
    sx, sy = width / cw, height / ch
    x0, y0, x1, y1 = platform["safe"]
    b0, b1 = platform["caption_band"]
    return {
        **platform,
        "canvas": (width, height),
        "safe": (round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy)),
        "caption_band": (round(b0 * sy), round(b1 * sy)),
        "caption_y": round(platform["caption_y"] * sy),
    }


def main() -> None:
    for name, p in PLATFORMS.items():
        w, h = p["canvas"]
        x0, y0, x1, y1 = p["safe"]
        b0, b1 = p["caption_band"]
        print(f"{name:9s} {w}x{h}  safe x {x0}-{x1}, y {y0}-{y1}  captions y {b0}-{b1}  ({p['label']})")


if __name__ == "__main__":
    main()
