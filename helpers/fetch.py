"""Download video/audio assets with yt-dlp — the only way this skill downloads media.

Every download lands in <edit>/downloads/, is recorded in downloads/manifest.json
with its source URL, uploader and licence, and is cached: asking for the same
URL (and section) twice returns the file already on disk.

  url      download one link (footage the user owns, licensed stock, a reference
           video, a music bed with --audio-only). --section grabs just a slice.
  search   find b-roll on YouTube. Creative Commons only by default: results are
           filtered with YouTube's CC filter, then every candidate's licence is
           verified from its metadata. Writes a numbered thumbnail contact sheet.
  credits  write <edit>/credits.md for third-party media the EDL actually uses.

yt-dlp resolution: $VIDEO_EDIT_YTDLP, else a system yt-dlp >= 2025.11.12, else
`uvx --from "yt-dlp[default]@latest" yt-dlp` (no install needed when uv exists).
YouTube needs a JavaScript runtime since late 2025: Deno is used when present,
otherwise Node >= 22 is enabled automatically.

Usage:
    python helpers/fetch.py url <URL> --edit-dir <edit> [--purpose broll] [--section 1:05-1:20]
    python helpers/fetch.py url <URL> --edit-dir <edit> --audio-only --purpose music
    python helpers/fetch.py search "city skyline night drone" --edit-dir <edit> [--n 8] [--max-duration 300]
    python helpers/fetch.py credits --edit-dir <edit> [--edl <edit>/edl.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote_plus

MIN_YTDLP = (2025, 11, 12)
CC_FILTER = "EgIwAQ%3D%3D"   # YouTube search filter: Creative Commons
USABLE = {"cc-by", "cc0", "user-provided"}


# -------- yt-dlp resolution ---------------------------------------------------


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text.strip().splitlines()[0] if text.strip() else "")
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def _version_of(cmd: list[str]) -> tuple[int, ...]:
    try:
        out = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=180)
        return parse_version(out.stdout) if out.returncode == 0 else (0,)
    except (OSError, subprocess.TimeoutExpired):
        return (0,)


def resolve_ytdlp() -> tuple[list[str], tuple[int, ...], str]:
    """(command, version, origin)."""
    env = os.environ.get("VIDEO_EDIT_YTDLP")
    if env:
        cmd = shlex.split(env)
        return cmd, _version_of(cmd), "env"
    system = shutil.which("yt-dlp")
    sys_version = _version_of([system]) if system else (0,)
    if system and sys_version >= MIN_YTDLP:
        return [system], sys_version, "system"
    uvx = shutil.which("uvx")
    if uvx:
        cmd = [uvx, "--from", "yt-dlp[default]@latest", "yt-dlp"]
        v = _version_of(cmd)
        if v >= MIN_YTDLP:
            if system:
                print(f"note: system yt-dlp {'.'.join(map(str, sys_version))} is too old for YouTube; "
                      f"using uvx yt-dlp {'.'.join(map(str, v))}. Upgrade with: brew upgrade yt-dlp",
                      file=sys.stderr)
            return cmd, v, "uvx"
    if system:
        print(f"WARNING: yt-dlp {'.'.join(map(str, sys_version))} is older than 2025.11.12 — YouTube "
              f"downloads will likely fail. Upgrade: brew upgrade yt-dlp  (or pip install -U 'yt-dlp[default]')",
              file=sys.stderr)
        return [system], sys_version, "system-outdated"
    sys.exit("yt-dlp not found. Install it (macOS: brew install yt-dlp deno; anywhere: "
             "pip install -U 'yt-dlp[default]'), or install uv so it can run via uvx.")


def _node_major() -> int:
    node = shutil.which("node")
    if not node:
        return 0
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(re.findall(r"\d+", out)[0])
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return 0


def runtime_flags(version: tuple[int, ...], origin: str) -> list[str]:
    """JS runtime flags for YouTube's challenges (only on yt-dlp versions that know them)."""
    if version < MIN_YTDLP:
        return []
    flags: list[str] = []
    if not shutil.which("deno") and _node_major() >= 22:
        flags += ["--js-runtimes", "node"]
    if origin in ("system", "env"):
        # Homebrew and distro builds do not bundle the challenge solver scripts; allow
        # yt-dlp to fetch them from its own GitHub repo when it needs them.
        flags += ["--remote-components", "ejs:github"]
    return flags


class YtDlp:
    def __init__(self) -> None:
        self.cmd, self.version, self.origin = resolve_ytdlp()
        self.flags = runtime_flags(self.version, self.origin)

    def run(self, args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
        return subprocess.run(self.cmd + self.flags + args, capture_output=True, text=True, timeout=timeout)

    def json(self, args: list[str], timeout: int = 300) -> dict:
        proc = self.run(args, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(_explain(proc.stderr))
        return json.loads(proc.stdout)


def _explain(stderr: str) -> str:
    tail = "\n".join(l for l in stderr.strip().splitlines()[-6:])
    hints = []
    if "Sign in to confirm" in stderr or "not a bot" in stderr:
        hints.append("YouTube wants a signed-in session. Only if the user explicitly agrees, retry with "
                     "VIDEO_EDIT_YTDLP='yt-dlp --cookies-from-browser chrome' (reads their browser cookies).")
    if "Requested format is not available" in stderr:
        hints.append("Try --max-height 720, or update yt-dlp.")
    if "HTTP Error 403" in stderr or "nsig" in stderr or "challenge" in stderr.lower():
        hints.append("Usually an outdated yt-dlp or a missing JS runtime: brew upgrade yt-dlp && brew install deno.")
    if "Private video" in stderr or "members-only" in stderr:
        hints.append("The video is private/members-only — ask the user for the file instead.")
    return tail + ("\n  hint: " + "\n  hint: ".join(hints) if hints else "")


# -------- licence ---------------------------------------------------------------


def classify_license(info: dict) -> str:
    """cc-by | cc0 | cc-restricted (NC/ND: not for commercial or edited use) | other | unknown."""
    lic = (info.get("license") or "").strip().lower()
    if not lic:
        return "unknown"
    if "creative commons" in lic or lic.startswith(("cc", "by")):
        if "zero" in lic or "cc0" in lic or "public domain" in lic:
            return "cc0"
        if re.search(r"\b(nc|nd)\b|non-?commercial|no-?deriv", lic):
            return "cc-restricted"
        return "cc-by"
    if "public domain" in lic:
        return "cc0"
    return "other"


# -------- manifest ----------------------------------------------------------------


def downloads_dir(edit_dir: Path) -> Path:
    d = edit_dir / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_manifest(edit_dir: Path) -> dict:
    p = downloads_dir(edit_dir) / "manifest.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"items": []}


def save_manifest(edit_dir: Path, manifest: dict) -> None:
    (downloads_dir(edit_dir) / "manifest.json").write_text(json.dumps(manifest, indent=2))


def slugify(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n].rstrip("-") or "media")


def parse_section(spec: str) -> tuple[float, float]:
    """'1:05-1:20' / '65-80' / '00:01:05.5-00:01:20' → seconds."""
    def secs(t: str) -> float:
        parts = [float(p) for p in t.strip().split(":")]
        v = 0.0
        for p in parts:
            v = v * 60 + p
        return v
    a, b = spec.split("-", 1)
    start, end = secs(a), secs(b)
    if end <= start:
        raise ValueError(f"section end must be after start: {spec}")
    return start, end


def probe(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return {}
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "duration": round(float(data.get("format", {}).get("duration", 0) or 0), 3),
        "width": v.get("width"), "height": v.get("height"), "fps": v.get("avg_frame_rate"),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


# -------- url ---------------------------------------------------------------------


def download(
    url: str,
    edit_dir: Path,
    purpose: str = "broll",
    section: str | None = None,
    audio_only: bool = False,
    max_height: int = 1080,
    name: str | None = None,
    user_provided: bool = True,
    ytdlp: YtDlp | None = None,
) -> dict:
    y = ytdlp or YtDlp()
    info = y.json(["--dump-single-json", "--no-playlist", "--skip-download", url])
    vid = str(info.get("id") or slugify(url))
    sec = parse_section(section) if section else None
    manifest = load_manifest(edit_dir)
    key = {"extractor": info.get("extractor_key"), "id": vid,
           "section": list(sec) if sec else None, "audio_only": audio_only}
    for item in manifest["items"]:
        if all(item.get(k) == v for k, v in key.items()) and Path(item["file"]).exists():
            print(f"cached: {item['file']}")
            return item

    base = f"{slugify(name or info.get('title') or vid)}-{vid}"
    if sec:
        base += f"_{sec[0]:g}-{sec[1]:g}"
    out_tmpl = str(downloads_dir(edit_dir) / f"{base}.%(ext)s")
    args = ["--no-playlist", "--no-overwrites", "-o", out_tmpl, "--print", "after_move:filepath"]
    if audio_only:
        args += ["-f", "ba/b", "-x"]
    else:
        args += ["-f", "bv*+ba/b", "-S", f"res:{max_height},+codec:avc:m4a", "--merge-output-format", "mp4"]
    if sec:
        args += ["--download-sections", f"*{sec[0]:.2f}-{sec[1]:.2f}", "--force-keyframes-at-cuts"]
    print(f"downloading {url}" + (f" [{section}]" if section else "") + f" via yt-dlp {'.'.join(map(str, y.version))} ({y.origin})")
    proc = y.run(args + [url])
    if proc.returncode != 0:
        raise RuntimeError(_explain(proc.stderr))
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    if not lines or not Path(lines[-1]).exists():
        raise RuntimeError(f"yt-dlp finished but no file was reported\n{_explain(proc.stderr)}")
    path = Path(lines[-1]).resolve()

    licence = classify_license(info)
    item = {
        **key,
        "url": info.get("webpage_url") or url,
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "uploader_url": info.get("uploader_url") or info.get("channel_url"),
        "license": info.get("license"),
        "license_class": licence,
        "rights": licence if licence in ("cc-by", "cc0") else ("user-provided" if user_provided else licence),
        "purpose": purpose,
        "file": str(path),
        "downloaded_at": dt.datetime.now().isoformat(timespec="seconds"),
        **probe(path),
    }
    manifest["items"].append(item)
    save_manifest(edit_dir, manifest)
    print(f"saved: {path}")
    dims = f" {item['width']}x{item['height']}" if item.get("width") else ""
    print(f"  {item.get('duration')}s{dims}  licence: {item['license'] or 'none listed'} → {item['rights']}")
    if item["rights"] == "cc-restricted":
        print("  WARNING: NonCommercial/NoDerivatives licence — not usable in a brand/commercial edit.")
    elif item["rights"] == "user-provided":
        print("  note: not Creative Commons. Use it only if the user owns it or holds the rights.")
    return item


# -------- search ------------------------------------------------------------------


def search(
    query: str,
    edit_dir: Path,
    n: int = 8,
    cc_only: bool = True,
    max_duration: float | None = None,
    min_height: int = 720,
) -> list[dict]:
    y = YtDlp()
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    if cc_only:
        url += f"&sp={CC_FILTER}"
    data = y.json(["--flat-playlist", "--dump-single-json", "--playlist-end", str(n * 3), url])
    entries = [e for e in data.get("entries") or [] if e.get("id")]
    if max_duration:
        entries = [e for e in entries if (e.get("duration") or 0) <= max_duration]

    def verify(e: dict) -> dict | None:
        try:
            info = y.json(["--dump-single-json", "--no-playlist", "--skip-download",
                           f"https://www.youtube.com/watch?v={e['id']}"], timeout=180)
        except Exception:
            return None
        height = max((f.get("height") or 0) for f in info.get("formats") or [{}])
        return {
            "id": e["id"], "url": info.get("webpage_url"), "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel"), "duration": info.get("duration"),
            "height": height, "license": info.get("license"), "license_class": classify_license(info),
            "thumbnail": info.get("thumbnail") or ((e.get("thumbnails") or [{}])[-1].get("url")),
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [r for r in pool.map(verify, entries[: n * 2]) if r]
    if cc_only:
        results = [r for r in results if r["license_class"] in ("cc-by", "cc0")]
    results = [r for r in results if (r["height"] or 0) >= min_height][:n]

    sdir = downloads_dir(edit_dir) / "search"
    sdir.mkdir(parents=True, exist_ok=True)
    slug = slugify(query, 32)
    (sdir / f"{slug}.json").write_text(json.dumps({"query": query, "cc_only": cc_only, "results": results}, indent=2))
    sheet = contact_sheet(results, sdir / f"{slug}.jpg")

    print(f"{len(results)} result(s) for '{query}'" + (" (Creative Commons, licence verified)" if cc_only else ""))
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['duration'] or '?':>5}s {r['height']}p  {r['license_class']:6s}  {r['uploader'] or '?'} — "
              f"{(r['title'] or '')[:60]}\n       {r['url']}")
    if sheet:
        print(f"  thumbnails: {sheet}")
    print("Pick with the thumbnails + titles, then: fetch.py url <URL> --purpose broll --section a-b")
    return results


def contact_sheet(results: list[dict], out: Path) -> Path | None:
    if not results:
        return None
    try:
        import io
        import requests
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    tw, th, cols = 384, 216, 4
    rows = (len(results) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 30)), (20, 20, 24))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except OSError:
        font = ImageFont.load_default()
    for i, r in enumerate(results):
        x, yy = (i % cols) * tw, (i // cols) * (th + 30)
        try:
            img = Image.open(io.BytesIO(requests.get(r["thumbnail"], timeout=20).content)).convert("RGB")
            img = img.resize((tw, th))
            sheet.paste(img, (x, yy))
        except Exception:
            pass
        d.rectangle((x, yy, x + 44, yy + 30), fill=(0, 0, 0))
        d.text((x + 8, yy + 4), str(i + 1), fill=(255, 255, 255), font=font)
        d.text((x + 6, yy + th + 4), f"{r['duration'] or '?'}s {r['height']}p  {(r['title'] or '')[:26]}",
               fill=(220, 220, 220), font=font)
    sheet.save(out, quality=85)
    return out


# -------- credits -----------------------------------------------------------------


def used_files(edl_path: Path) -> set[str]:
    edl = json.loads(edl_path.read_text())
    base = edl_path.parent
    files = set()
    for p in list((edl.get("sources") or {}).values()) + [o.get("file") for o in edl.get("overlays") or []] \
            + [a.get("file") for a in edl.get("audio") or []]:
        if p:
            q = Path(p)
            files.add(str((q if q.is_absolute() else (base / q)).resolve()))
    return files


def write_credits(edit_dir: Path, edl_path: Path | None = None) -> Path:
    items = load_manifest(edit_dir)["items"]
    if edl_path and edl_path.exists():
        used = used_files(edl_path)
        items = [i for i in items if str(Path(i["file"]).resolve()) in used]
    lines = ["# Credits", "", "Third-party media in this edit (from downloads/manifest.json).", ""]
    for i in items:
        who = i.get("uploader") or "unknown"
        lic = i.get("license") or ("provided by the client" if i.get("rights") == "user-provided" else "no licence listed")
        lines.append(f"- \"{i.get('title')}\" by {who} — {i.get('url')} — {lic} [{i.get('purpose')}]")
    flagged = [i for i in items if i.get("rights") not in USABLE]
    if flagged:
        lines += ["", "## Needs a rights check before publishing", ""]
        lines += [f"- {i.get('title')} — {i.get('url')} ({i.get('rights')})" for i in flagged]
    out = edit_dir / "credits.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"credits → {out} ({len(items)} item(s){', ' + str(len(flagged)) + ' flagged' if flagged else ''})")
    return out


# -------- CLI ---------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Download media with yt-dlp (cached, licence-tracked)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("url", help="Download one link")
    u.add_argument("url")
    u.add_argument("--edit-dir", type=Path, required=True)
    u.add_argument("--purpose", default="broll", help="broll | footage | reference | music | sfx | logo")
    u.add_argument("--section", help="Only this slice, e.g. 1:05-1:20")
    u.add_argument("--audio-only", action="store_true")
    u.add_argument("--max-height", type=int, default=1080)
    u.add_argument("--name", help="Short name for the file")
    u.add_argument("--found-by-search", action="store_true",
                   help="The link came from fetch.py search, not from the user (rights not assumed)")

    s = sub.add_parser("search", help="Find Creative Commons b-roll on YouTube")
    s.add_argument("query")
    s.add_argument("--edit-dir", type=Path, required=True)
    s.add_argument("--n", type=int, default=8)
    s.add_argument("--max-duration", type=float, default=None, help="Skip videos longer than this (s)")
    s.add_argument("--min-height", type=int, default=720)
    s.add_argument("--any-license", action="store_true",
                   help="Include non-CC results (only when the user has said they hold the rights)")

    c = sub.add_parser("credits", help="Write credits.md for downloaded media the EDL uses")
    c.add_argument("--edit-dir", type=Path, required=True)
    c.add_argument("--edl", type=Path, default=None)

    args = ap.parse_args()
    edit_dir = args.edit_dir.resolve()
    try:
        if args.cmd == "url":
            download(args.url, edit_dir, purpose=args.purpose, section=args.section,
                     audio_only=args.audio_only, max_height=args.max_height, name=args.name,
                     user_provided=not args.found_by_search)
        elif args.cmd == "search":
            search(args.query, edit_dir, n=args.n, cc_only=not args.any_license,
                   max_duration=args.max_duration, min_height=args.min_height)
        else:
            write_credits(edit_dir, args.edl.resolve() if args.edl else (edit_dir / "edl.json"))
    except RuntimeError as e:
        sys.exit(f"yt-dlp failed:\n{e}")


if __name__ == "__main__":
    main()
