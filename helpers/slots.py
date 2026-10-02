"""Graphic slots: set up the folders parallel sub-agents build in, then QA their renders.

  prepare  for every slot in <edit>/animations/slots.json create <edit>/animations/<id>/:
             TASK.md    what to build: exact window, canvas, layout zone, brief, paths
             slot.json  the same, machine-readable (check reads it)
             words.txt  words spoken during the slot, seconds from slot start
             refs/      3 frames of the cut underneath (start / middle / end)
             brand.css + fonts/ + logos/   (brand_kit.py install)
           and <edit>/animations/SLOT_BRIEF.md (shared rules) if missing.
           Prints one sub-agent prompt per slot.
  check    composite a slot render over the cut at its window with the safe zone (red)
           and caption band (yellow) drawn, write <slot>/check_sheet.jpg, and verify
           size, fps, duration, alpha, transparent first/last frame and safe-zone spill.

slots.json:
  [{"id": "s1_hook", "start": 0.0, "duration": 3.5, "layout": "top",
    "brief": "Kinetic title 'Why Meta wins' — 'Meta' lands at 0.9 s, 'wins' at 1.4 s"}]
  layouts: full | top | lower_third | split | corner

Usage:
    python helpers/slots.py prepare --edit-dir <edit> [--platform reels]
    python helpers/slots.py check <edit>/animations/s1_hook
    python helpers/slots.py check <render.mov> --start 12.5 --edit-dir <edit> [--layout split]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from captions import output_words  # noqa: E402
from platforms import get_platform, platform_for_canvas, scaled  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
LAYOUTS = ("full", "top", "lower_third", "split", "corner")
ALPHA_PIX = ("yuva", "gbra", "rgba", "bgra", "argb", "abgr", "ya")


def layout_zone(layout: str, plat: dict) -> tuple[int, int, int, int]:
    """Where a slot's content may go, inside the platform safe zone."""
    w, h = plat["canvas"]
    x0, y0, x1, y1 = plat["safe"]
    b0, _ = plat["caption_band"]
    if layout == "top":
        return x0, y0, x1, y0 + int((y1 - y0) * 0.30)
    if layout == "lower_third":
        return x0, max(y0, b0 - int(h * 0.17)), x1, b0 - int(h * 0.01)
    if layout == "split":
        return x0, y0, x1, h // 2 - 30
    if layout == "corner":
        return x1 - int(w * 0.22), y0, x1, y0 + int(h * 0.09)
    return x0, y0, x1, y1


LAYOUT_NOTES = {
    "full": "Full-screen graphic. Background fills may run edge to edge; text and marks stay in the zone. "
            "Keep the caption band visually quiet (plain background only) while captions show.",
    "top": "Top card. Content only in the zone so the speaker's face stays visible below it.",
    "lower_third": "Name/title strap above the caption band. Animate in, hold, animate out.",
    "split": "Opaque panel covering the TOP HALF (y 0 to H/2, edge to edge); content in the zone. The "
             "compositor moves the speaker into the bottom half and captions sit on the seam — keep the "
             "bottom 30 px of the panel free of content.",
    "corner": "Small mark (logo bug, badge, counter) in the top-right corner of the safe zone.",
}


def find_base(edit_dir: Path) -> Path | None:
    for name in ("base.mp4", "base_preview.mp4", "base_draft.mp4"):
        if (edit_dir / name).exists():
            return edit_dir / name
    return None


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,pix_fmt,width,height,avg_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(out.stdout)
        s = data["streams"][0]
        num, den = (s.get("avg_frame_rate") or "0/1").split("/")
        return {
            "codec": s.get("codec_name"), "pix_fmt": s.get("pix_fmt"),
            "width": int(s["width"]), "height": int(s["height"]),
            "fps": float(num) / float(den or 1) if float(den or 1) else 0.0,
            "duration": float(data.get("format", {}).get("duration") or 0),
        }
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return {}


# -------- prepare ----------------------------------------------------------------


def cmd_prepare(args: argparse.Namespace) -> None:
    edit_dir = args.edit_dir.resolve()
    anim = edit_dir / "animations"
    slots_path = args.slots or (anim / "slots.json")
    if not slots_path.exists():
        sys.exit(f"no {slots_path} — write the slot list first (see this file's docstring)")
    slots = json.loads(slots_path.read_text())
    edl = json.loads((edit_dir / "edl.json").read_text())
    base = args.base or find_base(edit_dir)
    if base is None:
        sys.exit("no base.mp4 — run render.py <edl> -o <edit>/base.mp4 --base-only first")
    bp = probe(base)
    out = edl.get("output") or {}
    cw, ch = (int(out["width"]), int(out["height"])) if out.get("width") else (bp["width"], bp["height"])
    tl = edit_dir / "timeline.json"
    fps = json.loads(tl.read_text()).get("fps", "30") if tl.exists() else "30"
    fps_f = eval_fps(fps)
    platform = args.platform
    brand_json = edit_dir / "brand" / "brand.json"
    if platform is None and brand_json.exists():
        platform = json.loads(brand_json.read_text()).get("platform")
    plat = scaled(get_platform(platform or platform_for_canvas(cw, ch)), cw, ch)
    words = output_words(edl, edit_dir)

    brief_path = anim / "SLOT_BRIEF.md"
    anim.mkdir(parents=True, exist_ok=True)
    if not brief_path.exists():
        tmpl = (SKILL_DIR / "templates" / "SLOT_BRIEF.md").read_text()
        brief_path.write_text(tmpl.replace("{{EDIT_DIR}}", str(edit_dir)).replace("{{SKILL_DIR}}", str(SKILL_DIR)))

    prompts = []
    for s in slots:
        sid, start, dur = s["id"], float(s["start"]), float(s["duration"])
        layout = s.get("layout", "full")
        if layout not in LAYOUTS:
            sys.exit(f"slot {sid}: unknown layout '{layout}' (use {', '.join(LAYOUTS)})")
        d = anim / sid
        (d / "refs").mkdir(parents=True, exist_ok=True)
        zone = layout_zone(layout, plat)
        frames = round(dur * fps_f)

        lines = [f"{w['start'] - start:7.2f}  {w['text']}" for w in words
                 if start - 0.3 <= w["start"] <= start + dur + 0.3]
        (d / "words.txt").write_text(
            "# seconds from slot start → word (approximate: land reveals ~0.15 s before the listed time)\n"
            + "\n".join(lines) + "\n")

        for name, t in (("start", start + 0.05), ("mid", start + dur / 2), ("end", start + dur - 0.05)):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(base),
                            "-frames:v", "1", "-vf", "scale=trunc(iw/4)*2:-2",
                            str(d / "refs" / f"{name}.jpg")], check=False)

        hf_ok = scaffold_hyperframes(d, cw, ch, dur)
        if (edit_dir / "brand" / "brand.css").exists():
            subprocess.run([sys.executable, str(SKILL_DIR / "helpers" / "brand_kit.py"), "install", str(d / "hf"),
                            "--edit-dir", str(edit_dir)], check=False, stdout=subprocess.DEVNULL)

        meta = {"id": sid, "start": start, "duration": dur, "frames": frames, "fps": fps, "canvas": [cw, ch],
                "layout": layout, "zone": list(zone), "safe": list(plat["safe"]),
                "caption_band": list(plat["caption_band"]), "platform": plat["label"],
                "base": str(base), "crop_y": s.get("crop_y"), "brief": s.get("brief", ""), "scaffolded": hf_ok}
        (d / "slot.json").write_text(json.dumps(meta, indent=2))
        (d / "TASK.md").write_text(task_md(meta, d, edit_dir))
        prompts.append(f"Build graphic slot {sid}. Read {brief_path} and then {d / 'TASK.md'}. "
                       f"Work only inside {d}. Do not ask questions.")
        print(f"  {sid:20s} {start:7.2f}s +{dur:5.2f}s  {layout:11s} zone x {zone[0]}-{zone[2]}, y {zone[1]}-{zone[3]}"
              f"  ({len(lines)} words)")

    print(f"\nprepared {len(slots)} slot(s) in {anim}. Spawn one sub-agent per slot, all at once:")
    for p in prompts:
        print(f"  - {p}")


RESOLUTION_PRESETS = {(1080, 1920): "portrait", (1920, 1080): "landscape", (1080, 1080): "square",
                      (2160, 3840): "portrait-4k", (3840, 2160): "landscape-4k", (2160, 2160): "square-4k"}


def scaffold_hyperframes(slot_dir: Path, width: int, height: int, duration: float) -> bool:
    """`hyperframes init hf` inside the slot (init refuses non-empty folders, so the project
    lives in hf/), pre-set to the canvas, the slot duration and a transparent background."""
    hf = slot_dir / "hf"
    if (hf / "index.html").exists():
        return True
    cmd = ["npx", "--yes", "hyperframes", "init", "hf", "--example", "blank", "--non-interactive"]
    if (width, height) in RESOLUTION_PRESETS:
        cmd += ["--resolution", RESOLUTION_PRESETS[(width, height)]]
    try:
        subprocess.run(cmd, cwd=slot_dir, env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1"},
                       capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return False
    index = hf / "index.html"
    if not index.exists():
        return False
    html = index.read_text()
    html = re.sub(r'data-duration="[0-9.]+"', f'data-duration="{duration:g}"', html)
    html = re.sub(r'data-width="\d+"', f'data-width="{width}"', html)
    html = re.sub(r'data-height="\d+"', f'data-height="{height}"', html)
    html = re.sub(r'content="width=\d+, height=\d+"', f'content="width={width}, height={height}"', html)
    html = re.sub(r"width: \d+px;(\s*)height: \d+px;", f"width: {width}px;\\1height: {height}px;", html, count=1)
    html = html.replace("background: #0a0a0a;", "background: transparent;")
    index.write_text(html)
    return True


def eval_fps(fps: str) -> float:
    if "/" in str(fps):
        a, b = str(fps).split("/")
        return float(a) / float(b)
    return float(fps)


def task_md(m: dict, d: Path, edit_dir: Path) -> str:
    x0, y0, x1, y1 = m["zone"]
    sx0, sy0, sx1, sy1 = m["safe"]
    b0, b1 = m["caption_band"]
    w, h = m["canvas"]
    return f"""# Graphic slot `{m['id']}`

Build ONE transparent overlay animation. Nothing else.

| | |
|---|---|
| Output | `{d / 'render.mov'}` — HyperFrames `--format mov` (ProRes 4444 with alpha) |
| Canvas | {w}x{h} @ {m['fps']} fps |
| Duration | EXACTLY {m['duration']:.3f} s ({m['frames']} frames) |
| Window in the final video | {m['start']:.3f} – {m['start'] + m['duration']:.3f} s |
| Layout | **{m['layout']}** — content inside **x {x0}–{x1}, y {y0}–{y1}** |
| Safe zone ({m['platform']}) | x {sx0}–{sx1}, y {sy0}–{sy1}; caption band y {b0}–{b1} |

{LAYOUT_NOTES[m['layout']]}

## Brief

{m['brief'] or '(see the editor notes)'}

## Inputs (all in this folder)

- `words.txt` — what is said during the slot, seconds from slot start. Land reveals on the words.
- `refs/start.jpg`, `refs/mid.jpg`, `refs/end.jpg` — the footage underneath (half scale).
- `hf/` — the HyperFrames project: {"already scaffolded at this canvas and duration with a transparent background" if m.get("scaffolded") else "NOT scaffolded yet — run: cd " + str(d) + " && HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes init hf --example blank --non-interactive, then set the canvas, duration and transparent background"}.
  The brand kit is in `hf/brand.css`, `hf/fonts/`, `hf/logos/`. Binding rules: `{edit_dir / 'brand' / 'DESIGN.md'}`.

## Deliver

1. Author `hf/index.html`, then render from `hf/`:
   `npx --yes hyperframes lint . && npx --yes hyperframes render . --format mov -o ../render.mov`
2. QA: `uv run --project {SKILL_DIR} {SKILL_DIR / 'helpers' / 'slots.py'} check {d}` → read `check_sheet.jpg` and fix until clean.
3. `NOTES.md`: what it shows and its timeline in slot seconds.
"""


# -------- check ----------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> None:
    target = args.target.resolve()
    if target.is_dir():
        meta = json.loads((target / "slot.json").read_text())
        render = next((target / n for n in ("render.mov", "render.webm", "render.mp4") if (target / n).exists()), None)
        if render is None:
            sys.exit(f"no render.mov in {target}")
        slot_dir = target
    else:
        if args.start is None or args.edit_dir is None:
            sys.exit("checking a bare file needs --start and --edit-dir")
        render, slot_dir = target, target.parent
        edit_dir = args.edit_dir.resolve()
        base = find_base(edit_dir)
        bp = probe(base)
        plat = scaled(get_platform(args.platform or platform_for_canvas(bp["width"], bp["height"])), bp["width"], bp["height"])
        meta = {"start": args.start, "duration": None, "layout": args.layout or "full", "base": str(base),
                "canvas": [bp["width"], bp["height"]], "safe": list(plat["safe"]),
                "caption_band": list(plat["caption_band"]), "crop_y": args.crop_y, "fps": None}

    info = probe(render)
    problems: list[str] = []
    w, h = meta["canvas"]
    print(f"{render.name}: {info.get('codec')} {info.get('pix_fmt')} {info.get('width')}x{info.get('height')} "
          f"@ {info.get('fps', 0):.3f} fps, {info.get('duration', 0):.3f} s")
    if (info.get("width"), info.get("height")) != (w, h):
        problems.append(f"size {info.get('width')}x{info.get('height')} ≠ canvas {w}x{h}")
    pix = info.get("pix_fmt") or ""
    if not pix.startswith(ALPHA_PIX) and render.suffix != ".mp4":
        problems.append(f"no alpha channel (pix_fmt {info.get('pix_fmt')}) — render with --format mov")
    if meta.get("fps") and abs(info.get("fps", 0) - eval_fps(meta["fps"])) > 0.01:
        problems.append(f"fps {info.get('fps'):.3f} ≠ {meta['fps']}")
    if meta.get("duration") and abs(info.get("duration", 0) - meta["duration"]) > 1.5 / max(1, info.get("fps", 30)):
        problems.append(f"duration {info.get('duration'):.3f} s ≠ {meta['duration']:.3f} s")

    dur = meta.get("duration") or info.get("duration") or 1.0
    with tempfile.TemporaryDirectory() as tmp:
        first, last = Path(tmp) / "first.png", Path(tmp) / "last.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(render), "-frames:v", "1",
                        "-pix_fmt", "rgba", str(first)], check=False)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.2", "-i", str(render), "-update", "1",
                        "-pix_fmt", "rgba", str(last)], check=False)
        from PIL import Image
        for label, p in (("first", first), ("last", last)):
            if p.exists():
                with Image.open(p) as im:
                    amax = im.getchannel("A").getextrema()[1] if im.mode == "RGBA" else 255
                if amax > 8:
                    problems.append(f"{label} frame is not fully transparent (max alpha {amax})")

        # safe-zone spill: any visible pixel outside the zone (full/split may paint background to the edges)
        spill = 0
        if meta["layout"] not in ("full", "split"):
            sx0, sy0, sx1, sy1 = meta["safe"]
            for k in range(6):
                t = dur * (k + 0.5) / 6
                fp = Path(tmp) / f"s{k}.png"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(render),
                                "-frames:v", "1", "-pix_fmt", "rgba", str(fp)], check=False)
                if fp.exists():
                    with Image.open(fp) as im:
                        a = im.getchannel("A")
                        inside = a.crop((sx0, sy0, sx1, sy1)).histogram()
                        total = a.histogram()
                        visible_total = sum(total[17:])
                        visible_inside = sum(inside[17:])
                        spill = max(spill, visible_total - visible_inside)
            if spill > 200:
                problems.append(f"{spill} visible pixels outside the safe zone")

        # contact sheet over the real footage
        sx0, sy0, sx1, sy1 = meta["safe"]
        b0, b1 = meta["caption_band"]
        base = Path(meta["base"])
        bp = probe(base)
        s = bp["width"] / w
        base_chain = "[0:v]null[bb]"
        if meta["layout"] == "split":
            half = bp["height"] // 2
            cy = int(round((meta.get("crop_y") or h * 0.2) * s))
            base_chain = f"[0:v]split[a][b];[b]crop={bp['width']}:{half}:0:{cy}[c];[a][c]overlay=0:{half}[bb]"
        n = 8
        fps = info.get("fps") or 30
        sel = "+".join(f"eq(n,{int((i + 0.5) * dur / n * fps)})" for i in range(n))
        sheet = slot_dir / "check_sheet.jpg"
        graph = (f"{base_chain};[1:v]format=yuva444p10le,scale={bp['width']}:{bp['height']}[o];"
                 f"[bb][o]overlay=0:0:format=auto,"
                 f"drawbox=x={int(sx0 * s)}:y={int(sy0 * s)}:w={int((sx1 - sx0) * s)}:h={int((sy1 - sy0) * s)}:color=red@0.9:t=3,"
                 f"drawbox=x=0:y={int(b0 * s)}:w={bp['width']}:h={int((b1 - b0) * s)}:color=yellow@0.5:t=3,"
                 f"select='{sel}',scale=360:-2,tile=4x2")
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{meta['start']:.3f}", "-t", f"{dur:.3f}",
                               "-i", str(base), "-i", str(render), "-filter_complex", graph,
                               "-frames:v", "1", "-update", "1", str(sheet)], capture_output=True, text=True)
        if proc.returncode != 0:
            problems.append(f"contact sheet failed: {proc.stderr.strip()[-300:]}")
        else:
            print(f"contact sheet → {sheet}  (red: safe zone, yellow: caption band) — open it and look")

    if problems:
        for p in problems:
            print(f"  PROBLEM: {p}")
        sys.exit(1)
    print("  checks passed: size, fps, duration, alpha, transparent first/last frame, safe zone")


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare and QA graphic slots")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--edit-dir", type=Path, required=True)
    p.add_argument("--slots", type=Path, default=None, help="Default: <edit>/animations/slots.json")
    p.add_argument("--base", type=Path, default=None, help="Default: <edit>/base.mp4")
    p.add_argument("--platform", default=None, help="Safe-zone preset (default: brand.json platform, else from canvas)")
    p.set_defaults(fn=cmd_prepare)
    c = sub.add_parser("check")
    c.add_argument("target", type=Path, help="Slot folder (or a render file with --start)")
    c.add_argument("--start", type=float, default=None)
    c.add_argument("--edit-dir", type=Path, default=None)
    c.add_argument("--layout", choices=LAYOUTS, default=None)
    c.add_argument("--crop-y", type=int, default=None)
    c.add_argument("--platform", default=None)
    c.set_defaults(fn=cmd_check)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
