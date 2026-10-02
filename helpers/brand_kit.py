"""Brand kit: turn the brand documents a user drops in into tokens every graphic uses.

Workflow (details in references/brand.md):

  1. scan     inventory the brand files: pull text out of PDF / DOCX / PPTX / MD,
              extract images embedded in documents, rasterize SVG logos, read font
              names, and collect colour candidates (hex / RGB written in the docs,
              dominant colours of the logos). Writes <edit>/brand/scan.json.
  2. (you)    read the documents (Read tool on the PDFs or <edit>/brand/text/*.txt),
              look at the logo candidates, and write <edit>/brand/brand.json.
  3. build    validate brand.json, fetch missing Google Fonts, make the static
              caption font libass needs, check contrast, and write brand.css,
              DESIGN.md and board.png (the visual you show at the check-in).
  4. install  copy brand.css + fonts + logos into a graphics slot folder.

Extra tools:
  fonts    download a Google Font as static TTFs, one per weight
  pdf      render PDF pages to PNG, or one region with a transparent background
           (how you lift a vector logo out of a guidelines PDF)
  svg      rasterize an SVG to PNG
  alpha    knock a solid background colour out of a raster logo

Usage:
    python helpers/brand_kit.py scan <files-or-dirs...> --edit-dir <edit>
    python helpers/brand_kit.py build --edit-dir <edit> [--platform reels] [--frame still.jpg]
    python helpers/brand_kit.py install <slot_dir> --edit-dir <edit>
    python helpers/brand_kit.py fonts "DM Sans" --weights 400,700,800 -o <edit>/brand/fonts
    python helpers/brand_kit.py pdf guide.pdf --pages 1-4 -o <edit>/brand/pages
    python helpers/brand_kit.py pdf guide.pdf --page 3 --crop 0.1,0.2,0.5,0.4 --transparent -o logo.png
    python helpers/brand_kit.py svg logo.svg -o logo.png --width 1600
    python helpers/brand_kit.py alpha logo.jpg --knockout "#FFFFFF" -o logo.png
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from platforms import DEFAULT_PLATFORM, PLATFORMS, get_platform  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent

DOC_EXT = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".rtf", ".html", ".htm", ".css", ".json", ".yaml", ".yml"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".svgz", ".tif", ".tiff", ".bmp"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2", ".ttc"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aif", ".aiff"}
SKIP_DIRS = {"edit", "node_modules", ".venv", "venv", "__pycache__", ".git"}

LOGO_WORDS = ("logo", "logomark", "wordmark", "lockup", "brandmark", "monogram", "symbol", "mark", "icon", "badge")

WEIGHT_NAMES = {
    100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
    600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black", 1000: "ExtraBlack",
}


# ============================================================================
# Colours
# ============================================================================

HEX_RE = re.compile(r"(?<![0-9A-Za-z&])#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})(?![0-9A-Za-z])")
HEX_LABELED_RE = re.compile(r"\bHEX\s*[:\-]?\s*#?([0-9A-Fa-f]{6})\b", re.I)
RGB_RE = re.compile(r"\bRGB[A]?\s*[:\-]?\s*\(?\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})", re.I)
CMYK_RE = re.compile(r"\bCMYK\s*[:\-]?\s*\(?\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})\s*[,/ ]\s*(\d{1,3})", re.I)
PANTONE_RE = re.compile(r"\b(?:PANTONE|PMS)\s+[A-Z0-9][A-Z0-9 \-]{1,14}?\s?[CUM]?\b", re.I)


def norm_hex(value: str) -> str:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        raise ValueError(f"not a hex colour: {value!r}")
    return "#" + h.upper()


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = norm_hex(h)
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb[:3])


def rel_luminance(h: str) -> float:
    def ch(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((rel_luminance(a), rel_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def colors_in_text(text: str) -> list[tuple[str, str]]:
    """(hex, context line) for every colour written in a document."""
    hits: list[tuple[str, str]] = []
    for line in text.splitlines():
        ctx = " ".join(line.split())[:140]
        if not ctx:
            continue
        for m in HEX_RE.finditer(line):
            hits.append((norm_hex(m.group(1)), ctx))
        for m in HEX_LABELED_RE.finditer(line):
            hits.append((norm_hex(m.group(1)), ctx))
        for m in RGB_RE.finditer(line):
            r, g, b = (int(x) for x in m.groups())
            if max(r, g, b) <= 255:
                hits.append((rgb_to_hex((r, g, b)), ctx))
    # the same colour often appears as hex and RGB on one line: keep one hit per (hex, line)
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def dominant_colors(img: Image.Image, k: int = 6) -> list[dict]:
    """Main colours of an image, ignoring transparent pixels. [{hex, share}]."""
    im = img.convert("RGBA")
    im.thumbnail((240, 240))
    arr = np.asarray(im).reshape(-1, 4)
    arr = arr[arr[:, 3] >= 128][:, :3]
    if arr.size == 0:
        return []
    strip = Image.fromarray(arr.reshape(1, -1, 3).astype(np.uint8), "RGB")
    q = strip.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    palette = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)
    merged: list[list] = []   # [rgb, count]
    for count, idx in counts:
        rgb = tuple(palette[idx * 3: idx * 3 + 3])
        for m in merged:
            if sum((a - b) ** 2 for a, b in zip(m[0], rgb)) < 28 ** 2:
                m[1] += count
                break
        else:
            merged.append([rgb, count])
    total = sum(m[1] for m in merged) or 1
    merged.sort(key=lambda m: -m[1])
    return [{"hex": rgb_to_hex(m[0]), "share": round(m[1] / total, 3)} for m in merged[:k]]


# ============================================================================
# Documents
# ============================================================================


def _xml_text(xml: str, para_tag: str, text_tag: str) -> str:
    paras = re.split(rf"</{para_tag}>", xml)
    out = []
    for p in paras:
        runs = re.findall(rf"<{text_tag}(?:\s[^>]*)?>(.*?)</{text_tag}>", p, flags=re.S)
        line = "".join(runs).strip()
        if line:
            out.append(_unescape_xml(line))
    return "\n".join(out)


def _unescape_xml(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&"))


def extract_document(path: Path, brand_dir: Path) -> dict:
    """Text (saved to brand/text/<name>.txt) plus any images embedded in the document."""
    ext = path.suffix.lower()
    text, images, pages = "", [], None
    extracted_dir = brand_dir / "extracted"
    try:
        if ext == ".pdf":
            import pypdfium2 as pdfium
            import pypdfium2.raw as pdfium_c
            pdf = pdfium.PdfDocument(str(path))
            pages = len(pdf)
            chunks = []
            for i in range(pages):
                page = pdf[i]
                chunks.append(f"\n--- page {i + 1} ---\n" + page.get_textpage().get_text_range())
                for j, obj in enumerate(page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,))):
                    try:
                        w, h = obj.get_px_size()
                        if min(w, h) < 64:
                            continue
                        pil = obj.get_bitmap(render=False).to_pil()
                        extracted_dir.mkdir(parents=True, exist_ok=True)
                        dest = extracted_dir / f"{path.stem}_p{i + 1:02d}_{j:02d}.png"
                        pil.save(dest)
                        images.append(str(dest))
                    except Exception:
                        continue
            text = "".join(chunks)
        elif ext in (".docx", ".pptx"):
            with zipfile.ZipFile(path) as z:
                if ext == ".docx":
                    text = _xml_text(z.read("word/document.xml").decode("utf8", "ignore"), "w:p", "w:t")
                    media_prefix = "word/media/"
                else:
                    slides = sorted(
                        (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]),
                    )
                    parts = []
                    for n in slides:
                        parts.append(f"\n--- {Path(n).stem} ---\n"
                                     + _xml_text(z.read(n).decode("utf8", "ignore"), "a:p", "a:t"))
                    text = "".join(parts)
                    media_prefix = "ppt/media/"
                for n in z.namelist():
                    if n.startswith(media_prefix) and Path(n).suffix.lower() in IMG_EXT:
                        extracted_dir.mkdir(parents=True, exist_ok=True)
                        dest = extracted_dir / f"{path.stem}_{Path(n).name}"
                        dest.write_bytes(z.read(n))
                        images.append(str(dest))
        elif ext == ".rtf":
            raw = path.read_text(errors="ignore")
            text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
        else:
            text = path.read_text(errors="ignore")
            if ext in (".html", ".htm"):
                text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I) + "\n" + \
                    "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S | re.I))
    except Exception as e:  # a broken document should not stop the scan
        return {"file": str(path), "error": f"{type(e).__name__}: {e}", "images": images}

    text_dir = brand_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{path.stem}.txt"
    text_path.write_text(text, encoding="utf-8")

    fonts_mentioned = sorted({
        " ".join(m.split())[:80]
        for m in re.findall(r"font-family\s*:\s*([^;{}\n]+)", text, flags=re.I)
    })
    font_lines = [
        " ".join(l.split())[:140] for l in text.splitlines()
        if re.search(r"\b(typeface|typography|font|type family|primary type|secondary type)\b", l, re.I)
    ][:20]
    return {
        "file": str(path),
        "kind": ext.lstrip("."),
        "pages": pages,
        "chars": len(text),
        "text_file": str(text_path),
        "images": images,
        "css_font_families": fonts_mentioned,
        "font_lines": font_lines,
        "cmyk": sorted({" ".join(m.group(0).split()) for m in CMYK_RE.finditer(text)})[:20],
        "pantone": sorted({" ".join(m.group(0).split()) for m in PANTONE_RE.finditer(text)})[:20],
        "_text": text,
    }


# ============================================================================
# Images and logos
# ============================================================================


def rasterize_svg(svg: Path, out: Path, width: int = 1600, background: str | None = None) -> Path:
    import resvg_py
    kwargs = {"svg_path": str(svg), "width": width}
    if background:
        kwargs["background"] = background
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(resvg_py.svg_to_bytes(**kwargs)))
    return out


def scan_image(path: Path, brand_dir: Path) -> dict:
    info: dict = {"file": str(path)}
    raster = path
    if path.suffix.lower() in (".svg", ".svgz"):
        try:
            raster = rasterize_svg(path, brand_dir / "logos" / f"{path.stem}.png")
            info["png"] = str(raster)
            svg_text = path.read_text(errors="ignore") if path.suffix.lower() == ".svg" else ""
            info["svg_colors"] = sorted({norm_hex(m) for m in HEX_RE.findall(svg_text)})
        except Exception as e:
            info["error"] = f"svg rasterize failed: {e}"
            return info
    try:
        with Image.open(raster) as im:
            info["size"] = list(im.size)
            info["alpha"] = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            info["colors"] = dominant_colors(im)
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
        return info

    name = path.stem.lower()
    score = 0
    score += 3 if any(wd in name for wd in LOGO_WORDS) else 0
    score += 2 if path.suffix.lower() in (".svg", ".svgz") else 0
    score += 1 if info.get("alpha") else 0
    score += 1 if len(info.get("colors", [])) <= 4 else 0
    w, h = info["size"]
    score -= 2 if (w * h > 3_000_000 and len(info.get("colors", [])) >= 6) else 0   # big photo
    info["logo_score"] = score
    return info


def knockout(img: Image.Image, color: str, tolerance: int = 24, feather: int = 24) -> Image.Image:
    """Make pixels near `color` transparent with a soft edge."""
    im = img.convert("RGBA")
    arr = np.asarray(im).astype(np.float32)
    target = np.array(hex_to_rgb(color), dtype=np.float32)
    dist = np.sqrt(((arr[:, :, :3] - target) ** 2).sum(axis=2))
    alpha = np.clip((dist - tolerance) / max(1, feather), 0, 1) * arr[:, :, 3]
    arr[:, :, 3] = alpha
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def trim_alpha(img: Image.Image, pad: int = 4) -> Image.Image:
    bbox = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bbox:
        return img
    x0, y0, x1, y1 = bbox
    return img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)))


# ============================================================================
# Fonts
# ============================================================================


def font_info(path: Path) -> dict:
    from fontTools.ttLib import TTFont
    font = TTFont(str(path), fontNumber=0, lazy=True)
    name = font["name"]

    def n(i: int) -> str | None:
        v = name.getDebugName(i)
        return v.strip() if v else None

    os2 = font["OS/2"] if "OS/2" in font else None
    sub = n(17) or n(2) or ""
    info = {
        "file": str(path),
        "family": n(16) or n(1),
        "subfamily": sub,
        "legacy_family": n(1),
        "full_name": n(4),
        "postscript": n(6),
        "weight": int(os2.usWeightClass) if os2 else 400,
        "italic": bool(os2 and os2.fsSelection & 1) or "italic" in sub.lower(),
        "variable": "fvar" in font,
        "axes": [],
    }
    if info["variable"]:
        info["axes"] = [
            {"tag": a.axisTag, "min": a.minValue, "default": a.defaultValue, "max": a.maxValue}
            for a in font["fvar"].axes
        ]
    font.close()
    return info


def to_ttf(src: Path, dest_dir: Path) -> Path:
    """Copy a font into dest_dir; WOFF/WOFF2 are decompressed to TTF/OTF (libass and PIL want sfnt)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if ext in (".woff", ".woff2"):
        from fontTools.ttLib import TTFont
        font = TTFont(str(src))
        font.flavor = None
        out = dest_dir / (src.stem + (".otf" if "CFF " in font or "CFF2" in font else ".ttf"))
        font.save(str(out))
        return out
    out = dest_dir / src.name
    if src.resolve() != out.resolve():
        shutil.copy2(src, out)
    return out


def google_fonts(family: str, weights: list[int], out_dir: Path, italic: bool = False) -> list[Path]:
    """Download a Google Font as static TTFs (one per weight). Returns the files written.

    The css2 API serves TTF to non-browser clients, and generates a static instance
    per requested weight — exactly what libass and PIL need.
    """
    import requests

    def fetch_css(ws: list[int]) -> str | None:
        if italic:
            spec = "ital,wght@" + ";".join(f"{i},{w}" for i in (0, 1) for w in ws)
        else:
            spec = "wght@" + ";".join(str(w) for w in ws)
        url = f"https://fonts.googleapis.com/css2?family={family.strip().replace(' ', '+')}:{spec}"
        r = requests.get(url, headers={"User-Agent": "video-edit/1.0"}, timeout=30)
        return r.text if r.status_code == 200 else None

    css = fetch_css(sorted(set(weights)))
    if css is None:   # one unavailable weight fails the whole request: try them one by one
        css = "".join(c for c in (fetch_css([w]) for w in sorted(set(weights))) if c)
    if not css:
        raise LookupError(f"'{family}' is not on Google Fonts (or none of weights {weights} exist)")

    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for block in re.findall(r"@font-face\s*{(.*?)}", css, flags=re.S):
        url = re.search(r"url\((https://[^)]+)\)", block)
        weight = re.search(r"font-weight:\s*(\d+)", block)
        style = re.search(r"font-style:\s*(\w+)", block)
        if not url or not weight:
            continue
        w = int(weight.group(1))
        is_italic = bool(style and style.group(1) == "italic")
        ext = Path(url.group(1).split("?")[0]).suffix or ".ttf"
        dest = out_dir / f"{family.replace(' ', '')}-{WEIGHT_NAMES.get(w, str(w))}{'Italic' if is_italic else ''}{ext}"
        if not dest.exists():
            r = requests.get(url.group(1), timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
        files.append(dest)
    return files


def find_font_file(fonts_dir: Path, family: str, weight: int, italic: bool = False) -> Path | None:
    """Best local file for family+weight: a variable font covering the weight, else nearest static."""
    best, best_d = None, 10_000
    for f in sorted(fonts_dir.glob("*")):
        if f.suffix.lower() not in (".ttf", ".otf") or not f.is_file():
            continue
        try:
            info = font_info(f)
        except Exception:
            continue
        want = family.lower()
        names = [(info.get("family") or "").lower(), (info.get("legacy_family") or "").lower()]
        if not any(n == want or n.startswith(want + " ") for n in names if n):
            continue
        if bool(info["italic"]) != bool(italic):
            continue
        wght = next((a for a in info["axes"] if a["tag"] == "wght"), None)
        if wght and wght["min"] <= weight <= wght["max"]:
            return f
        d = abs(info["weight"] - weight)
        if d < best_d:
            best, best_d = f, d
    return best


def static_caption_font(src: Path, weight: int, out_dir: Path, family: str, opsz: float | None = None) -> tuple[Path, str]:
    """A static, uniquely named copy of the caption font for libass. Returns (file, name to use).

    libass can hang on variable fonts, and a family name shared with other installed
    faces makes it pick the wrong one silently. The instance gets an explicit
    family/full name "<Family> <Weight>" so the ASS style can name it exactly.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*"):
        if old.is_file():
            old.unlink()
    font = TTFont(str(src))
    if "fvar" in font:
        limits = {}
        for a in font["fvar"].axes:
            if a.axisTag == "wght":
                limits["wght"] = max(a.minValue, min(a.maxValue, weight))
            elif a.axisTag == "opsz" and opsz is not None:
                limits["opsz"] = max(a.minValue, min(a.maxValue, opsz))
            else:
                limits[a.axisTag] = a.defaultValue
        font = instancer.instantiateVariableFont(font, limits)
    weight_name = WEIGHT_NAMES.get(int(round(weight / 100.0)) * 100, str(weight))
    unique = f"{family} {weight_name}"
    ps = re.sub(r"[^A-Za-z0-9-]", "", f"{family.replace(' ', '')}-{weight_name}")[:62]
    name = font["name"]
    for nid in (16, 17, 21, 22, 25):
        name.removeNames(nameID=nid)
    for nid, value in ((1, unique), (2, "Regular"), (4, unique), (6, ps), (3, f"video-edit:{ps}")):
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    if "OS/2" in font:
        font["OS/2"].usWeightClass = int(weight)
        font["OS/2"].fsSelection &= ~0b1100001   # clear italic/bold flags; set REGULAR
        font["OS/2"].fsSelection |= 0b1000000
    if "head" in font:
        font["head"].macStyle = 0
    ext = ".otf" if ("CFF " in font or "CFF2" in font) else ".ttf"
    out = out_dir / f"{ps}{ext}"
    font.save(str(out))
    return out, unique


def css_font_format(path: Path) -> str:
    return {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}.get(path.suffix.lower(), "truetype")


# ============================================================================
# scan
# ============================================================================


def collect_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        p = p.expanduser().resolve()
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                rel = f.relative_to(p).parts
                if any(part.startswith(".") or part in SKIP_DIRS for part in rel):
                    continue
                if f.is_file():
                    files.append(f)
    return files


def cmd_scan(args: argparse.Namespace) -> None:
    edit_dir = args.edit_dir.resolve()
    brand_dir = edit_dir / "brand"
    brand_dir.mkdir(parents=True, exist_ok=True)
    files = collect_files(args.paths)
    if not files:
        sys.exit("no files found to scan")

    result: dict = {"documents": [], "images": [], "fonts": [], "videos": [], "audio": [], "other": []}
    color_hits: dict[str, dict] = {}

    def add_color(hexv: str, ctx: str, source: str) -> None:
        e = color_hits.setdefault(hexv, {"hex": hexv, "count": 0, "contexts": [], "sources": []})
        e["count"] += 1
        if ctx and ctx not in e["contexts"] and len(e["contexts"]) < 3:
            e["contexts"].append(ctx)
        if source not in e["sources"]:
            e["sources"].append(source)

    for f in files:
        ext = f.suffix.lower()
        if ext in DOC_EXT:
            doc = extract_document(f, brand_dir)
            for hexv, ctx in colors_in_text(doc.pop("_text", "")):
                add_color(hexv, ctx, f.name)
            result["documents"].append(doc)
            for img_path in doc.get("images", []):
                result["images"].append(scan_image(Path(img_path), brand_dir) | {"from_document": f.name})
        elif ext in IMG_EXT:
            info = scan_image(f, brand_dir)
            for hexv in info.get("svg_colors", []):
                add_color(hexv, f"fill in {f.name}", f.name)
            result["images"].append(info)
        elif ext in FONT_EXT:
            try:
                if ext == ".ttc":
                    from fontTools.ttLib import TTCollection
                    coll = TTCollection(str(f))
                    for i, font in enumerate(coll.fonts):
                        out = brand_dir / "fonts" / f"{f.stem}-{i}.ttf"
                        out.parent.mkdir(parents=True, exist_ok=True)
                        font.save(str(out))
                        result["fonts"].append(font_info(out) | {"original": str(f)})
                else:
                    out = to_ttf(f, brand_dir / "fonts")
                    result["fonts"].append(font_info(out) | {"original": str(f)})
            except Exception as e:
                result["fonts"].append({"file": str(f), "error": f"{type(e).__name__}: {e}"})
        elif ext in VIDEO_EXT:
            result["videos"].append(str(f))
        elif ext in AUDIO_EXT:
            result["audio"].append(str(f))
        else:
            result["other"].append(str(f))

    for img in result["images"]:
        for c in img.get("colors", [])[:4]:
            if c["share"] >= 0.04 and img.get("logo_score", 0) >= 2:
                add_color(c["hex"], f"{c['share']:.0%} of {Path(img['file']).name}", Path(img["file"]).name)

    result["images"].sort(key=lambda i: -i.get("logo_score", 0))
    result["colors"] = sorted(color_hits.values(), key=lambda c: (-len(c["sources"]), -c["count"]))
    (brand_dir / "scan.json").write_text(json.dumps(result, indent=2))

    print(f"scanned {len(files)} file(s) → {brand_dir / 'scan.json'}")
    for d in result["documents"]:
        if d.get("error"):
            print(f"  doc   {Path(d['file']).name}: ERROR {d['error']}")
            continue
        pages = f"{d['pages']} pages, " if d.get("pages") else ""
        print(f"  doc   {Path(d['file']).name}: {pages}{d['chars']} chars → {d['text_file']}"
              + (f", {len(d['images'])} embedded image(s)" if d["images"] else ""))
        if d.get("pantone"):
            print(f"        pantone: {', '.join(d['pantone'][:6])}")
    for i in result["images"][:12]:
        if i.get("error"):
            print(f"  img   {Path(i['file']).name}: ERROR {i['error']}")
            continue
        cols = " ".join(f"{c['hex']}({c['share']:.0%})" for c in i.get("colors", [])[:4])
        print(f"  img   {Path(i['file']).name}  {i['size'][0]}x{i['size'][1]}"
              f"{' alpha' if i.get('alpha') else ''}  logo-score {i['logo_score']}  {cols}")
    if len(result["images"]) > 12:
        print(f"  ... {len(result['images']) - 12} more image(s) in scan.json")
    for fo in result["fonts"]:
        if fo.get("error"):
            print(f"  font  {Path(fo['file']).name}: ERROR {fo['error']}")
            continue
        var = " variable " + ",".join(f"{a['tag']} {a['min']:g}-{a['max']:g}" for a in fo["axes"]) if fo["variable"] else ""
        print(f"  font  {Path(fo['file']).name}: '{fo['family']}' {fo['subfamily']} w{fo['weight']}{var}")
    for v in result["videos"]:
        print(f"  video {Path(v).name} (brand reference)")
    for a in result["audio"]:
        print(f"  audio {Path(a).name}")
    if result["colors"]:
        print("  colour candidates (most-cited first):")
        for c in result["colors"][:12]:
            ctx = c["contexts"][0] if c["contexts"] else ""
            print(f"    {c['hex']}  x{c['count']}  {ctx[:90]}")
    print("\nNext: read the documents, view the logo candidates, write brand.json, then run build.")


# ============================================================================
# build
# ============================================================================


def resolve_path(p: str, brand_dir: Path) -> Path:
    q = Path(p).expanduser()
    return q if q.is_absolute() else (brand_dir / q).resolve()


def rel_to(p: Path, base: Path) -> str:
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(p.resolve())


def normalize_colors(raw: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for role, v in (raw or {}).items():
        if role == "gradients":
            continue
        entry = {"hex": v} if isinstance(v, str) else dict(v)
        entry["hex"] = norm_hex(entry["hex"])
        out[role] = entry
    return out


def text_on(hexv: str, light: str = "#FFFFFF", dark: str = "#000000") -> tuple[str, float]:
    """Readable text colour for a swatch. Light text wins whenever it passes WCAG AA (4.5:1):
    the WCAG formula under-rates white on saturated mid-tones (it calls black "better" on most
    brand blues), and no designer would set black on them."""
    cl, cd = contrast(hexv, light), contrast(hexv, dark)
    if cl >= 4.5 or cl >= cd:
        return light, cl
    return dark, cd


def cmd_build(args: argparse.Namespace) -> None:
    edit_dir = args.edit_dir.resolve()
    brand_dir = edit_dir / "brand"
    brand_path = brand_dir / "brand.json"
    if not brand_path.exists():
        sys.exit(f"no {brand_path} — run scan, read the docs, then write brand.json (see references/brand.md)")
    brand = json.loads(brand_path.read_text())
    problems: list[str] = []
    warnings: list[str] = []

    # ---- colours
    try:
        colors = normalize_colors(brand.get("colors", {}))
    except (ValueError, KeyError) as e:
        sys.exit(f"brand.json colours: {e}")
    if not colors:
        problems.append("no colours in brand.json")
    gradients = (brand.get("colors") or {}).get("gradients") or brand.get("gradients") or []
    light = colors.get("background", {}).get("hex", "#FFFFFF")
    dark = colors.get("text", {}).get("hex", "#000000")
    if rel_luminance(light) < rel_luminance(dark):
        light, dark = dark, light
    if "text" in colors and "background" in colors:
        c = contrast(colors["text"]["hex"], colors["background"]["hex"])
        if c < 4.5:
            warnings.append(f"text {colors['text']['hex']} on background {colors['background']['hex']} "
                            f"has contrast {c:.1f}:1 (< 4.5) — hard to read on a phone")

    # ---- fonts: every role gets a real file
    fonts_dir = brand_dir / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    fonts = brand.get("fonts") or {}
    if "heading" not in fonts:
        problems.append("fonts.heading is missing")
    for role, spec in fonts.items():
        family = spec.get("family")
        weight = int(spec.get("weight", 400))
        italic = bool(spec.get("italic", False))
        if not family:
            problems.append(f"fonts.{role} has no family")
            continue
        path = resolve_path(spec["file"], brand_dir) if spec.get("file") else None
        if path and not path.exists():
            problems.append(f"fonts.{role}.file not found: {path}")
            continue
        if path is None:
            path = find_font_file(fonts_dir, family, weight, italic)
        if path is None and not args.offline:
            try:
                got = google_fonts(family, sorted({weight, 400, 700}), fonts_dir, italic=italic)
                path = find_font_file(fonts_dir, family, weight, italic) or (got[0] if got else None)
                if path:
                    spec["source"] = "google-fonts"
                    warnings.append(f"fonts.{role}: '{family}' was not in the brand files — downloaded it "
                                    f"from Google Fonts. Confirm it is the brand's face at the check-in.")
            except Exception as e:
                problems.append(f"fonts.{role}: '{family}' not provided and not downloadable ({e}). Ask the "
                                f"user for the font files, or agree a substitute and set fonts.{role}.family.")
        if path is None:
            if args.offline:
                problems.append(f"fonts.{role}: no file for '{family}' (offline)")
            continue
        if path.parent.resolve() != fonts_dir.resolve():
            path = to_ttf(path, fonts_dir)
        spec["file"] = rel_to(path, brand_dir)
        spec.setdefault("source", "brand-files")

    # ---- caption font: a static, uniquely named instance in fonts/static/
    caps = brand.setdefault("captions", {})
    cap_role = caps.get("font_role") or ("captions" if "captions" in fonts else "heading")
    cap_spec = fonts.get(cap_role) or {}
    if cap_spec.get("file"):
        cap_weight = int(caps.get("weight") or cap_spec.get("weight") or 700)
        try:
            cap_file, cap_name = static_caption_font(
                resolve_path(cap_spec["file"], brand_dir), cap_weight, fonts_dir / "static",
                cap_spec["family"], opsz=float(caps.get("size") or 72),
            )
            caps["ass_fontname"] = cap_name
            caps["fonts_dir"] = rel_to(fonts_dir / "static", brand_dir)
            caps["font_file"] = rel_to(cap_file, brand_dir)
        except Exception as e:
            problems.append(f"could not make the static caption font: {type(e).__name__}: {e}")

    # ---- logos
    logos = brand.get("logos") or []
    if not logos:
        warnings.append("no logos in brand.json — graphics will be type-only")
    for lg in logos:
        p = resolve_path(lg["file"], brand_dir)
        if not p.exists():
            problems.append(f"logo not found: {p}")
            continue
        if p.suffix.lower() in (".svg", ".svgz"):
            png = brand_dir / "logos" / f"{p.stem}.png"
            if not png.exists():
                try:
                    rasterize_svg(p, png)
                except Exception as e:
                    problems.append(f"could not rasterize {p.name}: {e}")
            if png.exists():
                lg["png"] = rel_to(png, brand_dir)
        logos_dir = brand_dir / "logos"
        if p.parent.resolve() != logos_dir.resolve():
            logos_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, logos_dir / p.name)
            p = logos_dir / p.name
        lg["file"] = rel_to(p, brand_dir)

    brand["fonts"] = fonts
    brand_path.write_text(json.dumps(brand, indent=2))

    # ---- verify libass really uses the caption font
    if caps.get("ass_fontname"):
        try:
            from captions import font_matches, verify_font, resolve_style
            style = resolve_style({"font": caps["ass_fontname"], "fonts_dir": str(brand_dir / caps["fonts_dir"])})
            picked = verify_font(style, 1080, 1920)
            if not font_matches(picked, brand_dir / caps["fonts_dir"]):
                problems.append(f"libass did not pick the caption font (got {picked}); captions would "
                                f"silently use another face")
        except Exception as e:
            warnings.append(f"caption font check skipped: {e}")

    platforms = args.platform or [brand.get("platform") or DEFAULT_PLATFORM]
    for p in platforms:
        if p not in PLATFORMS:
            sys.exit(f"unknown platform '{p}'. Known: {', '.join(PLATFORMS)}")

    write_css(brand, colors, gradients, brand_dir)
    write_design(brand, colors, gradients, platforms, brand_dir)
    try:
        write_board(brand, colors, gradients, brand_dir, frame=args.frame)
    except Exception as e:
        warnings.append(f"board.png failed: {type(e).__name__}: {e}")

    print(f"brand kit → {brand_dir}")
    print(f"  brand.css, DESIGN.md, board.png  (platforms: {', '.join(platforms)})")
    for role, spec in fonts.items():
        print(f"  font {role:9s} {spec.get('family')} {spec.get('weight', 400)}  ← {spec.get('file', '?')} [{spec.get('source', '?')}]")
    if caps.get("ass_fontname"):
        print(f"  captions font: '{caps['ass_fontname']}' in {caps['fonts_dir']}/")
    for role, c in colors.items():
        fg, ratio = text_on(c["hex"], light, dark)
        print(f"  colour {role:12s} {c['hex']}  {c.get('name', '')}  (text on it: {fg} {ratio:.1f}:1)")
    for w in warnings:
        print(f"  WARNING: {w}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    if problems:
        sys.exit(1)


def write_css(brand: dict, colors: dict, gradients: list, brand_dir: Path) -> Path:
    lines = ["/* generated by video-edit brand_kit.py build from brand.json — edit brand.json, not this file */"]
    seen: set[str] = set()
    for role, spec in (brand.get("fonts") or {}).items():
        if not spec.get("file") or spec["file"] in seen:
            continue
        seen.add(spec["file"])
        path = resolve_path(spec["file"], brand_dir)
        try:
            info = font_info(path)
        except Exception:
            continue
        wght = next((a for a in info["axes"] if a["tag"] == "wght"), None)
        weight = f"{wght['min']:g} {wght['max']:g}" if wght else str(info["weight"])
        lines.append(
            f'@font-face {{ font-family: "{spec["family"]}"; src: url("{rel_to(path, brand_dir)}") '
            f'format("{css_font_format(path)}"); font-weight: {weight}; '
            f'font-style: {"italic" if info["italic"] else "normal"}; font-display: block; }}'
        )
    # every other font file of the same families (e.g. static weights from Google Fonts)
    families = {spec.get("family") for spec in (brand.get("fonts") or {}).values()}
    for f in sorted((brand_dir / "fonts").glob("*")):
        rel = rel_to(f, brand_dir)
        if f.suffix.lower() not in (".ttf", ".otf") or rel in seen:
            continue
        try:
            info = font_info(f)
        except Exception:
            continue
        fam = next((x for x in families if x and x.lower() in {(info["family"] or "").lower(), (info["legacy_family"] or "").lower()}), None)
        if not fam or info["variable"]:
            continue
        seen.add(rel)
        lines.append(
            f'@font-face {{ font-family: "{fam}"; src: url("{rel}") format("{css_font_format(f)}"); '
            f'font-weight: {info["weight"]}; font-style: {"italic" if info["italic"] else "normal"}; font-display: block; }}'
        )
    lines.append(":root {")
    for role, c in colors.items():
        lines.append(f"  --brand-{_slug(role)}: {c['hex']};")
    for g in gradients:
        stops = ", ".join(norm_hex(s) for s in g.get("stops", []))
        lines.append(f"  --brand-gradient-{_slug(g.get('name', 'main'))}: linear-gradient({g.get('angle', 135)}deg, {stops});")
    for role, spec in (brand.get("fonts") or {}).items():
        fallback = spec.get("fallback") or "system-ui, sans-serif"
        lines.append(f'  --font-{_slug(role)}: "{spec.get("family")}", {fallback};')
        lines.append(f"  --font-{_slug(role)}-weight: {int(spec.get('weight', 400))};")
    for k, v in (brand.get("elements") or {}).items():
        if isinstance(v, (str, int, float)):
            lines.append(f"  --{_slug(k)}: {v}{'px' if isinstance(v, (int, float)) and not k.endswith(('opacity', 'scale')) else ''};")
    lines.append("}")
    out = brand_dir / "brand.css"
    out.write_text("\n".join(lines) + "\n")
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def write_design(brand: dict, colors: dict, gradients: list, platforms: list[str], brand_dir: Path) -> Path:
    template = (SKILL_DIR / "templates" / "DESIGN.md").read_text()
    name = brand.get("name", "Brand")
    light = colors.get("background", {}).get("hex", "#FFFFFF")
    dark = colors.get("text", {}).get("hex", "#000000")
    if rel_luminance(light) < rel_luminance(dark):
        light, dark = dark, light

    fmt_lines = []
    for p in platforms:
        pl = get_platform(p)
        w, h = pl["canvas"]
        x0, y0, x1, y1 = pl["safe"]
        b0, b1 = pl["caption_band"]
        fmt_lines.append(
            f"- **{pl['label']}** — canvas {w}x{h}. Safe zone **x {x0}–{x1}, y {y0}–{y1}**. "
            f"Caption band **y {b0}–{b1}** (reserved for burned-in captions; keep it visually quiet)."
        )

    pal = ["| Role | Name | Hex | Use | Text on it |", "|---|---|---|---|---|"]
    for role, c in colors.items():
        fg, ratio = text_on(c["hex"], light, dark)
        pal.append(f"| {role} | {c.get('name', '')} | `{c['hex']}` | {c.get('use', '')} | `{fg}` ({ratio:.1f}:1) |")
    for g in gradients:
        pal.append(f"| gradient | {g.get('name', '')} | {' → '.join(norm_hex(s) for s in g.get('stops', []))} "
                   f"| {g.get('use', '')} | angle {g.get('angle', 135)}° |")

    type_lines = []
    heading = (brand.get("fonts") or {}).get("heading", {})
    for role, spec in (brand.get("fonts") or {}).items():
        type_lines.append(f"- **{role}**: `{spec.get('family')}` weight {spec.get('weight', 400)}"
                          f" — file `{spec.get('file', '?')}` ({spec.get('source', '')})"
                          + (f". {spec['notes']}" if spec.get("notes") else ""))
    font_check = f"{int(heading.get('weight', 700))} 64px \"{heading.get('family', 'sans-serif')}\""

    logo_lines = []
    for lg in brand.get("logos") or []:
        bits = [f"`{lg['file']}`"]
        if lg.get("png") and lg["png"] != lg["file"]:
            bits.append(f"(PNG: `{lg['png']}`)")
        for key in ("variant", "background", "use", "min_height_px", "clear_space"):
            if lg.get(key):
                bits.append(f"{key.replace('_', ' ')}: {lg[key]}")
        logo_lines.append("- " + " — ".join(bits))
    if not logo_lines:
        logo_lines.append("- (no logo files supplied — do not invent one; set the brand name in the heading font instead)")

    elements = brand.get("elements") or {}
    el_lines = [f"- {k.replace('_', ' ')}: `{v}`" for k, v in elements.items()] or ["- (none specified — keep shapes simple and consistent)"]

    motion = brand.get("motion") or {}
    motion_lines = [f"- {k.replace('_', ' ')}: {v}" for k, v in motion.items()] or [
        "- (brand gives no motion rules — use the defaults below)"]

    caps = brand.get("captions") or {}
    cap_lines = [
        f"- Font `{caps.get('ass_fontname', heading.get('family', '?'))}`, text `{caps.get('color', '#FFFFFF')}`, "
        f"active word `{caps.get('highlight', colors.get('primary', {}).get('hex', '#FFD84D'))}`, "
        f"mode {caps.get('mode', 'highlight')}, case {caps.get('case', 'natural')}.",
    ]

    voice = brand.get("voice") or "(not specified)"
    rules = brand.get("rules") or {}
    do = "\n".join(f"- {x}" for x in rules.get("do", [])) or "- (none listed)"
    dont = "\n".join(f"- {x}" for x in rules.get("dont", [])) or "- (none listed)"

    filled = (template
              .replace("{{BRAND_NAME}}", name)
              .replace("{{FORMATS}}", "\n".join(fmt_lines))
              .replace("{{PALETTE}}", "\n".join(pal))
              .replace("{{TYPE}}", "\n".join(type_lines))
              .replace("{{FONT_CHECK}}", font_check)
              .replace("{{LOGOS}}", "\n".join(logo_lines))
              .replace("{{ELEMENTS}}", "\n".join(el_lines))
              .replace("{{MOTION}}", "\n".join(motion_lines))
              .replace("{{CAPTIONS}}", "\n".join(cap_lines))
              .replace("{{VOICE}}", str(voice))
              .replace("{{DO}}", do)
              .replace("{{DONT}}", dont))
    out = brand_dir / "DESIGN.md"
    out.write_text(filled)
    return out


# ---- board -------------------------------------------------------------------


def _font(path: Path | None, size: int, weight: int | None = None) -> ImageFont.ImageFont:
    if path and path.exists():
        try:
            f = ImageFont.truetype(str(path), size)
            if weight:
                try:
                    axes = f.get_variation_axes()
                    f.set_variation_by_axes([
                        weight if b"eight" in (a.get("name") or b"") else a["default"] for a in axes
                    ])
                except Exception:
                    pass
            return f
        except Exception:
            pass
    for fallback in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size)
    return ImageFont.load_default()


def write_board(brand: dict, colors: dict, gradients: list, brand_dir: Path, frame: Path | None = None) -> Path:
    W, pad = 1600, 56
    light = colors.get("background", {}).get("hex", "#FAFAFA")
    dark = colors.get("text", {}).get("hex", "#111111")
    if rel_luminance(light) < rel_luminance(dark):
        light, dark = dark, light
    fonts = brand.get("fonts") or {}
    heading = fonts.get("heading", {})
    hpath = resolve_path(heading["file"], brand_dir) if heading.get("file") else None
    body = fonts.get("body", heading)
    bpath = resolve_path(body["file"], brand_dir) if body.get("file") else hpath

    label_font = _font(bpath, 22, int(body.get("weight", 400)))
    small_font = _font(bpath, 18, int(body.get("weight", 400)))
    sections: list[tuple[int, callable]] = []

    # title
    def draw_title(d: ImageDraw.ImageDraw, im: Image.Image, y: int) -> None:
        d.text((pad, y), brand.get("name", "Brand"), font=_font(hpath, 64, int(heading.get("weight", 800))), fill=dark)
        d.text((pad, y + 84), "Brand board — check colours, type, logos and captions before the edit",
               font=label_font, fill=_mix(dark, light, 0.45))
    sections.append((140, draw_title))

    # palette
    sw, gap = 200, 24
    per_row = max(1, (W - 2 * pad + gap) // (sw + gap))
    roles = list(colors.items())
    rows = (len(roles) + per_row - 1) // per_row

    def draw_palette(d, im, y):
        for i, (role, c) in enumerate(roles):
            x = pad + (i % per_row) * (sw + gap)
            yy = y + (i // per_row) * (sw + 96)
            d.rounded_rectangle((x, yy, x + sw, yy + sw), radius=24, fill=c["hex"],
                                outline=_mix(dark, light, 0.85), width=2)
            fg, ratio = text_on(c["hex"], light, dark)
            d.text((x + 16, yy + sw - 44), c["hex"], font=label_font, fill=fg)
            d.text((x, yy + sw + 10), role, font=label_font, fill=dark)
            d.text((x, yy + sw + 40), (c.get("name") or "")[:22], font=small_font, fill=_mix(dark, light, 0.4))
    sections.append((rows * (sw + 96) + 16, draw_palette))

    if gradients:
        def draw_gradients(d, im, y):
            for gi, g in enumerate(gradients):
                stops = [hex_to_rgb(s) for s in g.get("stops", [])] or [(0, 0, 0)]
                bar = _gradient_bar(W - 2 * pad, 90, stops)
                im.paste(bar, (pad, y + gi * 130))
                d.text((pad, y + gi * 130 + 96), f"{g.get('name', 'gradient')}: {' → '.join(norm_hex(s) for s in g.get('stops', []))}",
                       font=small_font, fill=dark)
        sections.append((len(gradients) * 130 + 10, draw_gradients))

    # type
    type_roles = [(r, s) for r, s in fonts.items() if s.get("file")]

    def draw_type(d, im, y):
        for i, (role, spec) in enumerate(type_roles):
            p = resolve_path(spec["file"], brand_dir)
            w = int(spec.get("weight", 400))
            yy = y + i * 150
            d.text((pad, yy), "Aa", font=_font(p, 96, w), fill=dark)
            d.text((pad + 190, yy + 8), "The quick brown fox jumps over the lazy dog 0123",
                   font=_font(p, 46, w), fill=dark)
            d.text((pad + 190, yy + 78), f"{role} — {spec.get('family')} {w} ({spec.get('source', '')})",
                   font=small_font, fill=_mix(dark, light, 0.4))
    sections.append((max(1, len(type_roles)) * 150 + 10, draw_type))

    # logos
    logos = brand.get("logos") or []

    def draw_logos(d, im, y):
        tile_w, tile_h = 340, 200
        for i, lg in enumerate(logos[:8]):
            x = pad + (i % 4) * (tile_w + 24)
            yy = y + (i // 4) * (tile_h + 50)
            bg = dark if (lg.get("background") == "dark") else light
            d.rounded_rectangle((x, yy, x + tile_w, yy + tile_h), radius=20, fill=bg, outline=_mix(dark, light, 0.85), width=2)
            src = resolve_path(lg.get("png") or lg["file"], brand_dir)
            try:
                with Image.open(src) as logo:
                    logo = logo.convert("RGBA")
                    logo.thumbnail((tile_w - 48, tile_h - 48))
                    im.paste(logo, (x + (tile_w - logo.width) // 2, yy + (tile_h - logo.height) // 2), logo)
            except Exception:
                d.text((x + 20, yy + 20), "cannot open", font=small_font, fill="#FF0000")
            d.text((x, yy + tile_h + 8), f"{Path(lg['file']).name}  {lg.get('variant', '')}", font=small_font, fill=dark)
    if logos:
        sections.append((((min(len(logos), 8) + 3) // 4) * 250 + 10, draw_logos))

    # captions preview
    caps = brand.get("captions") or {}

    def draw_captions(d, im, y):
        cw, ch = W - 2 * pad, 420
        if frame and frame.exists():
            with Image.open(frame) as f:
                f = f.convert("RGB")
                scale = max(cw / f.width, ch / f.height)
                f = f.resize((int(f.width * scale), int(f.height * scale)))
                left, top = (f.width - cw) // 2, int((f.height - ch) * 0.42)
                tile = f.crop((left, top, left + cw, top + ch))
        else:
            tile = _gradient_bar(cw, ch, [(70, 70, 78), (25, 25, 30)])
        im.paste(tile, (pad, y))
        cap_file = resolve_path(caps["font_file"], brand_dir) if caps.get("font_file") else hpath
        size = 76
        f = _font(cap_file, size)
        words = ["This", "is", "your", "caption"] if caps.get("case", "natural") != "upper" else ["THIS", "IS", "YOUR", "CAPTION"]
        widths = [d.textlength(wd + " ", font=f) for wd in words]
        x = pad + (cw - sum(widths)) / 2
        yy = y + ch - 150
        color = caps.get("color", "#FFFFFF")
        hl = caps.get("highlight", colors.get("primary", {}).get("hex", "#FFD84D"))
        stroke = max(2, int(size * 0.09))
        for i, wd in enumerate(words):
            fill = hl if i == 2 and caps.get("mode", "highlight") == "highlight" else color
            d.text((x, yy), wd, font=f, fill=fill, stroke_width=stroke, stroke_fill=caps.get("outline_color", "#000000"))
            x += widths[i]
        d.text((pad, y + ch + 10), f"captions: {caps.get('ass_fontname', '?')} · {caps.get('mode', 'highlight')} · "
               f"text {color} · active {hl}", font=small_font, fill=dark)
    sections.append((470, draw_captions))

    H = 40 + sum(h + 40 for h, _ in sections) + 20
    im = Image.new("RGB", (W, H), light)
    d = ImageDraw.Draw(im)
    y = 40
    for h, fn in sections:
        fn(d, im, y)
        y += h + 40
    out = brand_dir / "board.png"
    im.save(out, optimize=True)
    return out


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def _gradient_bar(w: int, h: int, stops: list[tuple[int, int, int]]) -> Image.Image:
    if len(stops) == 1:
        return Image.new("RGB", (w, h), stops[0])
    xs = np.linspace(0, len(stops) - 1, w)
    idx = np.clip(xs.astype(int), 0, len(stops) - 2)
    t = (xs - idx)[:, None]
    a = np.array([stops[i] for i in idx], dtype=np.float32)
    b = np.array([stops[i + 1] for i in idx], dtype=np.float32)
    row = (a + (b - a) * t).astype(np.uint8)
    return Image.fromarray(np.repeat(row[None, :, :], h, axis=0), "RGB")


# ============================================================================
# install / fonts / pdf / svg / alpha
# ============================================================================


def cmd_install(args: argparse.Namespace) -> None:
    brand_dir = args.edit_dir.resolve() / "brand"
    slot = args.slot_dir.resolve()
    slot.mkdir(parents=True, exist_ok=True)
    if not (brand_dir / "brand.css").exists():
        sys.exit("no brand.css — run brand_kit.py build first")
    shutil.copy2(brand_dir / "brand.css", slot / "brand.css")
    for sub in ("fonts", "logos", "assets"):
        src = brand_dir / sub
        if src.is_dir():
            shutil.copytree(src, slot / sub, dirs_exist_ok=True, ignore=shutil.ignore_patterns("static"))
    brand = json.loads((brand_dir / "brand.json").read_text())
    heading = (brand.get("fonts") or {}).get("heading", {})
    check = f"{int(heading.get('weight', 700))} 64px \"{heading.get('family', 'sans-serif')}\""
    print(f"installed brand kit into {slot}")
    print('  <link rel="stylesheet" href="brand.css">   colours: var(--brand-primary) …   fonts: var(--font-heading)')
    print(f"  assert before rendering: await document.fonts.ready; if (!document.fonts.check('{check}')) throw new Error('brand font missing');")


def cmd_fonts(args: argparse.Namespace) -> None:
    weights = [int(w) for w in args.weights.split(",") if w.strip()]
    files = google_fonts(args.family, weights, args.output.resolve(), italic=args.italic)
    for f in files:
        info = font_info(f)
        print(f"  {f}  ('{info['family']}' {info['subfamily']} w{info['weight']}, full name '{info['full_name']}')")


def _page_list(spec: str, n: int) -> list[int]:
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages += list(range(int(a), int(b) + 1))
        elif part:
            pages.append(int(part))
    return [p for p in pages if 1 <= p <= n]


def cmd_pdf(args: argparse.Namespace) -> None:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(args.pdf))
    n = len(pdf)
    fill = (255, 255, 255, 0) if args.transparent else (255, 255, 255, 255)
    if args.crop:
        if not args.page:
            sys.exit("--crop needs --page")
        x0, y0, x1, y1 = (float(v) for v in args.crop.split(","))
        page = pdf[args.page - 1]
        w, h = page.get_size()
        crop = (x0 * w, (1 - y1) * h, (1 - x1) * w, y0 * h)   # left, bottom, right, top amounts to cut
        img = page.render(scale=args.scale, crop=crop, fill_color=fill).to_pil()
        if args.knockout:
            img = knockout(img, args.knockout)
        if args.transparent or args.knockout:
            img = trim_alpha(img.convert("RGBA"))
        out = args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        print(f"  page {args.page} crop → {out} ({img.width}x{img.height})")
        return
    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = _page_list(args.pages, n) if args.pages else ([args.page] if args.page else list(range(1, n + 1)))
    for p in pages:
        img = pdf[p - 1].render(scale=args.scale, fill_color=fill).to_pil()
        dest = out_dir / f"{args.pdf.stem}_p{p:02d}.png"
        img.save(dest)
        print(f"  page {p} → {dest} ({img.width}x{img.height})")


def cmd_svg(args: argparse.Namespace) -> None:
    out = rasterize_svg(args.svg, args.output, width=args.width, background=args.background)
    print(f"  {args.svg} → {out}")


def cmd_alpha(args: argparse.Namespace) -> None:
    with Image.open(args.image) as im:
        img = trim_alpha(knockout(im, args.knockout, tolerance=args.tolerance))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.output)
    print(f"  {args.image} → {args.output} ({img.width}x{img.height}, {args.knockout} removed)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Brand kit: scan brand docs, build tokens, install into slots")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Inventory brand files and extract candidates")
    s.add_argument("paths", type=Path, nargs="+")
    s.add_argument("--edit-dir", type=Path, required=True)
    s.set_defaults(fn=cmd_scan)

    b = sub.add_parser("build", help="brand.json → brand.css, DESIGN.md, board.png, static caption font")
    b.add_argument("--edit-dir", type=Path, required=True)
    b.add_argument("--platform", action="append", help="Delivery format(s) for DESIGN.md (repeatable)")
    b.add_argument("--frame", type=Path, default=None, help="A still from the footage for the caption preview")
    b.add_argument("--offline", action="store_true", help="Do not download missing fonts")
    b.set_defaults(fn=cmd_build)

    i = sub.add_parser("install", help="Copy brand.css + fonts + logos into a slot folder")
    i.add_argument("slot_dir", type=Path)
    i.add_argument("--edit-dir", type=Path, required=True)
    i.set_defaults(fn=cmd_install)

    f = sub.add_parser("fonts", help="Download a Google Font as static TTFs")
    f.add_argument("family")
    f.add_argument("--weights", default="400,700")
    f.add_argument("--italic", action="store_true")
    f.add_argument("-o", "--output", type=Path, required=True)
    f.set_defaults(fn=cmd_fonts)

    p = sub.add_parser("pdf", help="Render PDF pages, or a crop (e.g. a vector logo), to PNG")
    p.add_argument("pdf", type=Path)
    p.add_argument("--pages", help="e.g. 1-3,7")
    p.add_argument("--page", type=int)
    p.add_argument("--crop", help="x0,y0,x1,y1 as fractions of the page, origin top-left")
    p.add_argument("--transparent", action="store_true", help="Transparent page background (trimmed)")
    p.add_argument("--knockout", help="Also remove this background colour, e.g. '#FFFFFF'")
    p.add_argument("--scale", type=float, default=2.0, help="Pixels per PDF point (2 ≈ 144 dpi)")
    p.add_argument("-o", "--output", type=Path, required=True, help="Folder for pages, file for --crop")
    p.set_defaults(fn=cmd_pdf)

    v = sub.add_parser("svg", help="Rasterize an SVG to PNG")
    v.add_argument("svg", type=Path)
    v.add_argument("-o", "--output", type=Path, required=True)
    v.add_argument("--width", type=int, default=1600)
    v.add_argument("--background", default=None)
    v.set_defaults(fn=cmd_svg)

    a = sub.add_parser("alpha", help="Knock a solid background colour out of a raster logo")
    a.add_argument("image", type=Path)
    a.add_argument("--knockout", required=True, help="Background colour, e.g. '#FFFFFF'")
    a.add_argument("--tolerance", type=int, default=24)
    a.add_argument("-o", "--output", type=Path, required=True)
    a.set_defaults(fn=cmd_alpha)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
