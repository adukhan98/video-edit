"""Branded burned-in captions: an ASS file on the output timeline.

Reads the per-source transcripts in <edit>/transcripts/, maps every kept word
onto the output timeline through the EDL (using the measured segment offsets in
<edit>/timeline.json when render.py has written one, so long edits do not
drift), groups words into short cues and writes <edit>/captions.ass styled from
the brand kit:

  - brand font, loaded from a folder of STATIC font files (libass can hang on
    variable fonts; brand_kit.py build makes the static instance)
  - brand text / highlight / outline colours
  - position on the platform's caption band, with optional per-window overrides
    (e.g. on the seam during a split-screen graphic)
  - modes: highlight (active word in the accent colour), plain, box

Style precedence: defaults < brand.json "captions" < EDL "captions" < CLI flags.

Usage:
    python helpers/captions.py --edl <edit>/edl.json
    python helpers/captions.py --edl <edit>/edl.json --brand <edit>/brand/brand.json
    python helpers/captions.py --edl <edit>/edl.json --mode box --case upper
    python helpers/captions.py --edl <edit>/edl.json --verify-font
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from platforms import get_platform, platform_for_canvas, scaled  # noqa: E402


# -------- Chunking (shared with render.py's SRT path) ------------------------

PUNCT_BREAK = set(".,!?;:")

CHUNK_WORDS = 2         # target words per cue
CHUNK_MAX_WORDS = 3     # a too-short chunk may grow to this many words
CHUNK_MIN_S = 0.35      # a cue shorter than this reads as a flash
CHUNK_PAUSE_S = 0.3     # a gap this long between words ends the cue


def chunk_words(
    words: list[dict],
    words_per_cue: int = CHUNK_WORDS,
    max_words: int = CHUNK_MAX_WORDS,
    min_cue_s: float = CHUNK_MIN_S,
    pause_s: float = CHUNK_PAUSE_S,
    max_chars: int | None = None,
) -> list[list[dict]]:
    """Group transcript words into caption cues.

    A cue closes on trailing punctuation or on a pause before the next word.
    Otherwise it closes at `words_per_cue` words, unless it would be on screen
    for less than `min_cue_s`; then it takes up to `max_words` words. With
    `max_chars`, a cue also closes before the next word would make it too wide.
    """
    words = [w for w in words if (w.get("text") or "").strip()]
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for i, w in enumerate(words):
        current.append(w)
        text = w["text"].strip()
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 0.0
        dur = w["end"] - current[0]["start"]
        too_wide = False
        if max_chars and nxt is not None:
            too_wide = len(" ".join(x["text"].strip() for x in current + [nxt])) > max_chars
        if (
            nxt is None
            or text[-1] in PUNCT_BREAK
            or gap >= pause_s
            or len(current) >= max_words
            or too_wide
            or (len(current) >= words_per_cue and dur >= min_cue_s)
        ):
            chunks.append(current)
            current = []
    return chunks


# -------- Transcript → output timeline ---------------------------------------


def load_transcript(edit_dir: Path, source_name: str, source_path: str | None = None) -> dict | None:
    """Find a source's transcript: <name>.json, <file stem>.json, or <stem>.trackN.json."""
    tdir = edit_dir / "transcripts"
    candidates = [tdir / f"{source_name}.json"]
    if source_path:
        stem = Path(source_path).stem
        candidates.append(tdir / f"{stem}.json")
        candidates += sorted(tdir.glob(f"{stem}.track*.json"))
    for c in candidates:
        if c.exists():
            return json.loads(c.read_text())
    return None


def segment_offsets(edl: dict, edit_dir: Path) -> list[float]:
    """Output-timeline start of every EDL range.

    Prefers the offsets render.py measured from the extracted segments
    (<edit>/timeline.json) — each segment is rounded to whole frames, and over
    dozens of cuts nominal offsets drift. Falls back to nominal durations when the
    timeline is missing or belongs to a different EDL.
    """
    ranges = edl["ranges"]
    tl_path = edit_dir / "timeline.json"
    if tl_path.exists():
        try:
            segs = json.loads(tl_path.read_text()).get("segments", [])
        except json.JSONDecodeError:
            segs = []
        if len(segs) == len(ranges) and all(
            s.get("source") == r["source"]
            and abs(float(s.get("start", -1)) - float(r["start"])) < 1e-3
            and abs(float(s.get("end", -1)) - float(r["end"])) < 1e-3
            for s, r in zip(segs, ranges)
        ):
            return [float(s["out_start"]) for s in segs]
    offsets, t = [], 0.0
    for r in ranges:
        offsets.append(t)
        t += float(r["end"]) - float(r["start"])
    return offsets


def output_words(edl: dict, edit_dir: Path, lag: float = 0.0) -> list[dict]:
    """Every spoken word kept by the EDL, with output-timeline start/end.

    A word belongs to a range when its midpoint falls inside it, so a word
    trimmed away by the cut padding never gets a caption.
    """
    out: list[dict] = []
    cache: dict[str, dict | None] = {}
    for r, offset in zip(edl["ranges"], segment_offsets(edl, edit_dir)):
        name = r["source"]
        if name not in cache:
            cache[name] = load_transcript(edit_dir, name, edl.get("sources", {}).get(name))
        transcript = cache[name]
        if not transcript:
            continue
        s0, s1 = float(r["start"]), float(r["end"])
        for w in transcript.get("words", []):
            if w.get("type", "word") != "word":
                continue
            ws, we = w.get("start"), w.get("end")
            text = (w.get("text") or "").strip()
            if ws is None or we is None or not text:
                continue
            if not (s0 <= (ws + we) / 2 <= s1):
                continue
            a = max(s0, ws) - s0 + offset - lag
            b = min(s1, we) - s0 + offset - lag
            out.append({"text": text, "start": max(0.0, a), "end": max(0.0, b), "source": name})
    out.sort(key=lambda x: x["start"])
    return out


# -------- Style ---------------------------------------------------------------

DEFAULT_STYLE: dict = {
    "mode": "highlight",        # highlight | plain | box
    "font": "Helvetica",        # family/full name as libass sees it (brand_kit.py writes ass_fontname)
    "fonts_dir": None,          # folder holding the STATIC caption font file(s)
    "size": None,               # px on the output canvas (None: 4.6% of a portrait height, 6% of landscape)
    "color": "#FFFFFF",         # cue text
    "highlight": "#FFD84D",     # active word (highlight mode)
    "outline_color": "#000000",
    "outline": None,            # px (None: 9% of size)
    "shadow": None,             # px (None: 3.5% of size)
    "shadow_color": "#000000",
    "shadow_alpha": 0.6,        # 0 opaque .. 1 invisible
    "box_color": "#000000",     # box mode background
    "box_alpha": 0.25,
    "case": "natural",          # upper | natural | lower | title
    "strip_punct": True,        # drop trailing , ; : . (keeps ? and !)
    "x": 0.5,                   # centre; <= 1 is a fraction of the canvas, else px
    "y": None,                  # None: the platform's caption line
    "words_per_cue": 2,
    "max_words": 3,
    "max_chars": 18,
    "pause_s": 0.3,
    "min_cue_s": 0.35,
    "max_cue_s": 2.6,
    "hold_s": 0.12,             # linger after the last word of a cue
    "pop": True,                # small scale pop when a cue appears
    "reveal": False,            # highlight mode: words appear as they are spoken (later words hidden, space reserved)
    "active_pop": 0,            # highlight mode: % the active word pops above its size, easing back in 110 ms (e.g. 12)
    "emphasis": {},             # {"$50": "#00D26A"} or [{"word": "$15", "color": "#4CC9F0", "start": 0, "end": 30}]
    "emphasis_scale": 112,      # % size of emphasis words (always coloured, not only while active)
    "soft_shadow": False,       # no hard stroke: a blurred dark shadow layer under the text (clean / minimal look)
    "letter_spacing": 0,
    "fixes": {},                # {"Mehta": "Meta"} exact-word corrections (brand names!)
    "censor": [],               # words shown as s**t
    "windows": [],              # [{"start": s, "end": e, "y": px}] position overrides
    "lag": 0.0,                 # shift all words earlier by this many seconds
    "platform": None,           # safe-zone preset; None: inferred from the canvas
}


def resolve_style(*layers: dict | None) -> dict:
    style = dict(DEFAULT_STYLE)
    for layer in layers:
        if not layer:
            continue
        for k, v in layer.items():
            if v is not None:
                style[k] = v
    return style


def ass_color(hex_color: str, transparency: float = 0.0) -> str:
    """'#RRGGBB' (or #RGB / #RRGGBBAA) → ASS '&HAABBGGRR'. transparency 0 = opaque."""
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 8:  # CSS #RRGGBBAA: AA is opacity
        transparency = 1 - int(h[6:8], 16) / 255
        h = h[:6]
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise ValueError(f"not a hex colour: {hex_color!r}")
    a = max(0, min(255, round(transparency * 255)))
    return f"&H{a:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_inline_color(hex_color: str) -> str:
    return ass_color(hex_color)[:2] + ass_color(hex_color)[4:] + "&"   # &HBBGGRR&


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_text(t: str) -> str:
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _censor(word: str) -> str:
    m = re.match(r"^(\W*)(\w+)(\W*)$", word)
    if not m or len(m.group(2)) < 3:
        return word
    core = m.group(2)
    return m.group(1) + core[0] + "*" * (len(core) - 2) + core[-1] + m.group(3)


def transform_word(word: str, style: dict) -> str:
    fixes = style.get("fixes") or {}
    if word in fixes:
        word = fixes[word]
    else:
        m = re.match(r"^(.*?)([.,!?;:]*)$", word)
        core, punct = (m.group(1), m.group(2)) if m else (word, "")
        if core in fixes:
            word = fixes[core] + punct
    censor = {c.lower() for c in style.get("censor") or []}
    if censor and re.sub(r"\W", "", word).lower() in censor:
        word = _censor(word)
    if style.get("strip_punct"):
        stripped = word.rstrip(",;:.")
        if stripped and "." not in stripped:   # keep "a.m." and "3.5"
            word = stripped
    case = style.get("case", "natural")
    if case == "upper":
        word = word.upper()
    elif case == "lower":
        word = word.lower()
    elif case == "title":
        word = word[:1].upper() + word[1:]
    return _escape_text(word)


def _px(value: float, extent: int) -> int:
    return int(round(value * extent)) if 0 <= value <= 1 else int(round(value))


# -------- ASS writer -----------------------------------------------------------

POP_TAG = r"{\fscx108\fscy108\t(0,90,\fscx100\fscy100)}"


def emphasis_color(word: str, t: float, style: dict) -> str | None:
    """Colour of an emphasis word at output time t, or None.

    `emphasis` is {word: hex} or a list of {"word", "color", "start"?, "end"?} so the same
    word can change colour over the video (e.g. "$15" blue in one section, coral later).
    Matching ignores case and trailing punctuation.
    """
    emph = style.get("emphasis") or {}
    key = re.sub(r"[.,!?;:]+$", "", word).lower()
    if isinstance(emph, dict):
        for k, v in emph.items():
            if k.lower() == key:
                return v
        return None
    for e in emph:
        if str(e.get("word", "")).lower() == key and float(e.get("start", 0)) <= t < float(e.get("end", 1e9)):
            return e.get("color")
    return None


def _rich_parts(ch: list[dict], texts: list[str], wi: int, style: dict, shadow: bool = False) -> str:
    """Highlight-mode text for word `wi` active, with reveal / active pop / emphasis."""
    hl, base = style["highlight"], style["color"]
    parts = []
    for j, (w, t) in enumerate(zip(ch, texts)):
        col = emphasis_color(w["text"], w["start"], style)
        sc = int(style.get("emphasis_scale", 112)) if col else 100
        hidden = style.get("reveal") and j > wi
        if shadow:
            alpha = "FF" if hidden else "30"
            parts.append(f"{{\\alpha&H{alpha}&\\fscx{sc}\\fscy{sc}}}{t}")
            continue
        if hidden:
            parts.append(f"{{\\alpha&HFF&\\fscx{sc}\\fscy{sc}}}{t}")
        elif j == wi:
            pop = int(style.get("active_pop") or 0)
            anim = (f"\\fscx{sc + pop}\\fscy{sc + pop}\\t(0,110,\\fscx{sc}\\fscy{sc})" if pop
                    else f"\\fscx{sc}\\fscy{sc}")
            parts.append(f"{{\\alpha&H00&\\c{ass_inline_color(col or hl)}{anim}}}{t}")
        else:
            parts.append(f"{{\\alpha&H00&\\c{ass_inline_color(col or base)}\\fscx{sc}\\fscy{sc}}}{t}")
    return " ".join(parts)


def _rich(style: dict) -> bool:
    return bool(style.get("reveal") or style.get("active_pop") or style.get("emphasis") or style.get("soft_shadow"))


def build_events(chunks: list[list[dict]], style: dict) -> list[tuple[float, float, str, bool]]:
    """(start, end, text, is_cue_start) per event. Highlight mode emits one event per word.

    With reveal / active_pop / emphasis / soft_shadow, the text carries per-word override
    tags; soft_shadow events come in pairs, the shadow copy's text prefixed with "\\0".
    """
    events: list[tuple[float, float, str, bool]] = []
    hl = ass_inline_color(style["highlight"])
    base = ass_inline_color(style["color"])
    rich = _rich(style)
    for ci, ch in enumerate(chunks):
        c0 = ch[0]["start"]
        nxt0 = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        c1 = ch[-1]["end"] + style["hold_s"]
        if nxt0 is not None:
            if nxt0 - c1 < 0.25:      # bridge tiny gaps instead of flickering
                c1 = nxt0
            c1 = min(c1, nxt0)
        c1 = max(min(c1, c0 + style["max_cue_s"]), ch[-1]["end"])
        if nxt0 is not None:
            c1 = min(c1, nxt0)
        texts = [transform_word(w["text"], style) for w in ch]
        if style["mode"] == "highlight":
            for wi, w in enumerate(ch):
                s = c0 if wi == 0 else w["start"]
                e = ch[wi + 1]["start"] if wi + 1 < len(ch) else c1
                if e - s < 0.02:
                    continue
                if rich:
                    if style.get("soft_shadow"):
                        events.append((s, e, "\0" + _rich_parts(ch, texts, wi, style, shadow=True), wi == 0))
                    events.append((s, e, _rich_parts(ch, texts, wi, style), wi == 0))
                    continue
                parts = [
                    f"{{\\c{hl}}}{t}{{\\c{base}}}" if j == wi else t
                    for j, t in enumerate(texts)
                ]
                events.append((s, e, " ".join(parts), wi == 0))
        else:
            if c1 - c0 >= 0.02:
                events.append((c0, c1, " ".join(texts), True))
    return events


def canvas_platform(style: dict, width: int, height: int) -> dict:
    name = style.get("platform") or platform_for_canvas(width, height)
    return scaled(get_platform(name), width, height)


def write_ass(chunks: list[list[dict]], style: dict, width: int, height: int, out_path: Path) -> int:
    plat = canvas_platform(style, width, height)
    size = style["size"] or round(height * (0.0458 if height > width else 0.06))
    outline = style["outline"] if style["outline"] is not None else round(size * 0.09, 1)
    shadow = style["shadow"] if style["shadow"] is not None else round(size * 0.035, 1)
    x = _px(style["x"], width)
    y_default = _px(style["y"], height) if style["y"] is not None else plat["caption_y"]

    if style.get("soft_shadow"):
        outline, shadow = 0, 0
    if style["mode"] == "box":
        border_style, outline_c, back_c = 3, ass_color(style["box_color"], style["box_alpha"]), "&HFF000000"
        outline = max(outline, round(size * 0.22))
        shadow = 0
    else:
        border_style = 1
        outline_c = ass_color(style["outline_color"])
        back_c = ass_color(style["shadow_color"], style["shadow_alpha"])
    primary = ass_color(style["color"])

    header = "\n".join([
        "[Script Info]",
        "; generated by video-edit helpers/captions.py",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{style['font']},{size},{primary},{primary},{outline_c},{back_c},"
        f"0,0,0,0,100,100,{style['letter_spacing']},0,{border_style},{outline},{shadow},5,0,0,0,1",
        f"Style: Shadow,{style['font']},{size},&H00000000,&H00000000,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,{style['letter_spacing']},0,1,{max(2, round(size * 0.045))},0,5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])

    windows = sorted(
        (float(w["start"]), float(w["end"]), _px(float(w["y"]), height))
        for w in style.get("windows") or []
    )
    rich = _rich(style)
    lines: list[str] = []
    for s, e, text, cue_start in build_events(chunks, style):
        is_shadow = text.startswith("\0")
        text = text.lstrip("\0")
        cuts = sorted({s, e, *[t for a, b, _ in windows for t in (a, b) if s < t < e]})
        for s2, e2 in zip(cuts, cuts[1:]):
            y = next((wy for a, b, wy in windows if a <= s2 + 0.005 < b), y_default)
            first = style.get("pop") and cue_start and s2 == s
            # rich text sets per-word scale, which would cancel a whole-line pop: fade the cue in instead
            pop = ("{\\fad(50,0)}" if rich else POP_TAG) if first else ""
            layer, name, extra = (0, "Shadow", "\\blur12") if is_shadow else (1 if rich else 0, "Cap", "")
            lines.append(
                f"Dialogue: {layer},{ass_time(s2)},{ass_time(e2)},{name},,0,0,0,,"
                f"{{\\an5\\pos({x},{y}){extra}}}{pop}{text}"
            )
    out_path.write_text(header + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# -------- ffmpeg helpers ------------------------------------------------------


def ff_quote(path: Path | str) -> str:
    """Quote a path for use as a filter option value inside -vf / -filter_complex."""
    s = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return "'" + s.replace("'", "'\\''") + "'"


def ass_filter(ass_path: Path, fonts_dir: Path | None) -> str:
    f = f"ass=filename={ff_quote(Path(ass_path).resolve())}"
    if fonts_dir:
        f += f":fontsdir={ff_quote(Path(fonts_dir).resolve())}"
    return f


def verify_font(style: dict, width: int, height: int) -> str | None:
    """Render one caption frame through libass and return the font file it actually picked.

    A missing font does not fail: libass silently falls back to another face. This
    is the only reliable check.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ass = Path(tmp) / "probe.ass"
        probe_style = dict(style, mode="plain", pop=False, windows=[])
        write_ass([[{"text": "Brand Check", "start": 0.0, "end": 1.0}]], probe_style, width, height, ass)
        fonts_dir = Path(style["fonts_dir"]) if style.get("fonts_dir") else None
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "info",
             "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d=0.5:r=10",
             "-vf", ass_filter(ass, fonts_dir), "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True,
        )
    for line in proc.stderr.splitlines():
        if "fontselect" in line and "->" in line:
            return line.split("->", 1)[1].strip()
    return None


def font_matches(picked: str | None, fonts_dir: str | Path | None) -> bool:
    """Did libass pick a font from fonts_dir? It reports system fonts by path but fonts
    loaded from fonts_dir by their PostScript name, so accept either."""
    if not picked or not fonts_dir:
        return bool(picked) and not fonts_dir
    fd = Path(fonts_dir).resolve()
    if str(fd) in picked:
        return True
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return False
    head = picked.split(",")[0].strip()
    for f in fd.glob("*"):
        if f.suffix.lower() not in (".ttf", ".otf"):
            continue
        try:
            font = TTFont(str(f), lazy=True)
            names = {font["name"].getDebugName(i) for i in (1, 4, 6)}
            font.close()
        except Exception:
            continue
        if head in names or f.stem == head:
            return True
    return False


def probe_canvas(edl: dict, edit_dir: Path) -> tuple[int, int]:
    """Output canvas: EDL output block, else the rendered base, else render.py's default scaling."""
    out = edl.get("output") or {}
    if out.get("width") and out.get("height"):
        return int(out["width"]), int(out["height"])
    for name in ("base.mp4", "base_preview.mp4", "base_draft.mp4"):
        p = edit_dir / name
        if p.exists():
            dims = _probe_dims(p)
            if dims:
                return dims
    first = edl["ranges"][0]["source"]
    src = Path(edl["sources"][first])
    if not src.is_absolute():
        src = (edit_dir / src).resolve()
    dims = _probe_dims(src, display=True)
    if not dims:
        return 1920, 1080
    w, h = dims
    if h > w:
        return round(1920 * w / h / 2) * 2, 1920
    return 1920, round(1920 * h / w / 2) * 2


def _probe_dims(video: Path, display: bool = False) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_side_data=rotation", "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        )
        s = json.loads(out.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        if display:
            rot = next((sd.get("rotation") for sd in s.get("side_data_list") or [] if sd.get("rotation") is not None), 0)
            if int(round(float(rot))) % 360 in (90, 270):
                w, h = h, w
        return w, h
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, IndexError, ValueError, OSError):
        return None


def load_brand_captions(brand_path: Path | None, edit_dir: Path) -> dict:
    """The brand kit's caption block, with its font folder resolved to an absolute path."""
    path = brand_path or (edit_dir / "brand" / "brand.json")
    if not path.exists():
        return {}
    brand = json.loads(path.read_text())
    caps = dict(brand.get("captions") or {})
    if caps.get("ass_fontname"):
        caps["font"] = caps.pop("ass_fontname")
    if caps.get("fonts_dir"):
        fd = Path(caps["fonts_dir"])
        caps["fonts_dir"] = str(fd if fd.is_absolute() else (path.parent / fd).resolve())
    return caps


def split_windows(edl: dict, height: int, explicit: list[dict]) -> list[dict]:
    """While a split-layout graphic covers the top half, the speaker sits in the bottom half —
    captions move up onto the seam instead of covering the face. Explicit windows win."""
    out = []
    for o in edl.get("overlays") or []:
        if o.get("layout") != "split":
            continue
        a = float(o["start_in_output"])
        b = a + float(o.get("duration") or 0)
        if any(float(w["start"]) <= a and float(w["end"]) >= b for w in explicit):
            continue
        out.append({"start": a, "end": b, "y": int(o.get("caption_y") or round(height * 0.54))})
    return out


def build_captions(
    edl: dict,
    edit_dir: Path,
    out_path: Path,
    brand_path: Path | None = None,
    overrides: dict | None = None,
    canvas: tuple[int, int] | None = None,
) -> dict:
    """Write the ASS file. Returns the resolved style plus stats (used by render.py)."""
    style = resolve_style(load_brand_captions(brand_path, edit_dir), edl.get("captions"), overrides)
    width, height = canvas or probe_canvas(edl, edit_dir)
    style["windows"] = list(style.get("windows") or []) + split_windows(edl, height, style.get("windows") or [])
    words = output_words(edl, edit_dir, lag=float(style.get("lag") or 0.0))
    chunks = chunk_words(
        words,
        words_per_cue=int(style["words_per_cue"]),
        max_words=int(style["max_words"]),
        min_cue_s=float(style["min_cue_s"]),
        pause_s=float(style["pause_s"]),
        max_chars=int(style["max_chars"]) if style.get("max_chars") else None,
    )
    n_events = write_ass(chunks, style, width, height, out_path)
    return {"style": style, "canvas": (width, height), "words": len(words), "cues": len(chunks), "events": n_events}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build branded ASS captions on the output timeline")
    ap.add_argument("--edl", type=Path, required=True, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output .ass (default: <edit>/captions.ass)")
    ap.add_argument("--brand", type=Path, default=None, help="brand.json (default: <edit>/brand/brand.json)")
    ap.add_argument("--mode", choices=["highlight", "plain", "box"])
    ap.add_argument("--font", help="Font family/full name as libass sees it")
    ap.add_argument("--fonts-dir", help="Folder with the static caption font")
    ap.add_argument("--size", type=int)
    ap.add_argument("--color")
    ap.add_argument("--highlight")
    ap.add_argument("--case", choices=["upper", "natural", "lower", "title"])
    ap.add_argument("--y", type=float, help="Caption centre line: px, or a fraction of the height")
    ap.add_argument("--max-words", type=int)
    ap.add_argument("--max-chars", type=int)
    ap.add_argument("--platform", help="Safe-zone preset (reels, tiktok, shorts, vertical, youtube, square, feed45)")
    ap.add_argument("--fix", action="append", default=[], metavar="WRONG=RIGHT", help="Word correction, repeatable")
    ap.add_argument("--censor", action="append", default=[], metavar="WORD", help="Word to censor, repeatable")
    ap.add_argument("--verify-font", action="store_true", help="Check which font file libass actually uses")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    edl = json.loads(edl_path.read_text())
    edit_dir = edl_path.parent
    out_path = (args.output or (edit_dir / "captions.ass")).resolve()

    overrides = {
        "mode": args.mode, "font": args.font, "fonts_dir": args.fonts_dir, "size": args.size,
        "color": args.color, "highlight": args.highlight, "case": args.case, "y": args.y,
        "max_words": args.max_words, "max_chars": args.max_chars, "platform": args.platform,
    }
    if args.fix:
        overrides["fixes"] = dict(f.split("=", 1) for f in args.fix if "=" in f)
    if args.censor:
        overrides["censor"] = args.censor

    info = build_captions(edl, edit_dir, out_path, brand_path=args.brand, overrides=overrides)
    w, h = info["canvas"]
    style = info["style"]
    print(f"captions → {out_path}  ({info['cues']} cues, {info['events']} events, {w}x{h}, "
          f"{style['mode']}, font '{style['font']}')")
    if args.verify_font:
        picked = verify_font(style, w, h)
        print(f"  libass font: {picked or 'UNKNOWN (no fontselect line)'}")
        if style.get("fonts_dir") and not font_matches(picked, style["fonts_dir"]):
            print("  WARNING: libass did not use a font from fonts_dir — the caption font name does not "
                  "match the file. Run brand_kit.py build and use the ass_fontname it prints.")


if __name__ == "__main__":
    main()
