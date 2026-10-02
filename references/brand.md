# Brand intake: from documents to `brand.json`

The brand kit is what makes every graphic, caption and colour in the edit consistent. You build it
once per project (reuse it across sessions) in three steps: **scan → read → build**.

## 1. Scan

```bash
uv run --project <skill_dir> <skill_dir>/helpers/brand_kit.py scan <brand files or folders> --edit-dir <project>/edit
```

It writes `edit/brand/scan.json` and prints a summary:

- **Documents** (PDF, DOCX, PPTX, MD, TXT, HTML/CSS, RTF): full text → `edit/brand/text/<name>.txt`,
  images embedded in them → `edit/brand/extracted/`, Pantone/CMYK mentions, lines that talk about type.
- **Images**: size, alpha, dominant colours, and a **logo score** (filename words like logo/mark/
  wordmark/lockup, SVG, transparency, few colours). SVGs are rasterized to `edit/brand/logos/<name>.png`.
- **Fonts**: family / style / weight / variable axes; WOFF/WOFF2 are converted to TTF into
  `edit/brand/fonts/`.
- **Colour candidates**: every hex/RGB written in the documents with the line it appeared on, plus the
  dominant colours of likely logos — most-cited first.
- Reference videos and audio inside the brand folder are listed, not processed. Look at reference
  videos with `timeline_view.py` for pacing, caption style and graphic language to match.

## 2. Read (you, not a script)

Text extraction misses what is only shown visually — swatches without written values, logo usage
diagrams, do/don't examples. So:

- **Open the guideline PDF with the Read tool** (it shows the pages; use `pages` for long PDFs) and
  read it like a designer: palette page, typography page, logo page, imagery/tone pages.
- For a swatch with no written value, render the page (`brand_kit.py pdf guide.pdf --page 4 -o
  edit/brand/pages`) and sample the colour from the PNG rather than guessing.
- **Vector logo only inside the PDF?** Render it out with a transparent background:
  `brand_kit.py pdf guide.pdf --page 3 --crop 0.12,0.20,0.48,0.38 --transparent --scale 6 -o edit/brand/logos/mark.png`
  (crop is x0,y0,x1,y1 as fractions of the page, top-left origin). If the page paints a white
  background, add `--knockout "#FFFFFF"`. Raster logo on a solid background:
  `brand_kit.py alpha logo.jpg --knockout "#FFFFFF" -o edit/brand/logos/logo.png`.
- Look at each logo candidate (Read the PNG). Decide variants: primary (for light backgrounds),
  reversed/white (for dark or photo backgrounds), mark-only, wordmark, horizontal/stacked lockups.

### Precedence when sources disagree

written guidelines > supplied logo/font files > the brand's website > your inference.
Write conflicts down and raise them at the check-in with your proposed resolution.

### Missing information — propose, don't stall

| Missing | Default to propose at the check-in |
|---|---|
| Palette | Logo colours (scan's dominant colours) + a near-black ink + a warm/cool off-white |
| Text/background colours | Darkest and lightest palette colours; check contrast ≥ 4.5:1 |
| Fonts named but not supplied | Google Fonts if it's there (`brand_kit.py build` downloads it); commercial fonts → ask for the files or propose the closest Google Font |
| No fonts at all | One clean sans in two weights (e.g. Inter / DM Sans / Manrope) matched to the logo's personality |
| Caption style | Heading font at 800, white text, active word in the primary or a lighter tint of it, black outline |
| Motion | "Snappy": power3.out entrances, back.out(1.6) pops, 0.25–0.45 s |
| No logo file | Never draw one. Set the brand name in the heading font and ask for the file |

## 3. Write `edit/brand/brand.json`

```json
{
  "name": "Muse",
  "platform": "reels",
  "source_docs": ["brand/Muse_Guidelines.pdf", "brand/logo.svg"],
  "colors": {
    "primary":    {"hex": "#0071F3", "name": "Muse Blue", "use": "accents, highlights"},
    "secondary":  {"hex": "#CBE5FE", "name": "Light Blue", "use": "tints, chat bubbles"},
    "background": {"hex": "#FAF9F5", "name": "Warm Off-white", "use": "cards"},
    "text":       {"hex": "#0F0F0D", "name": "Ink", "use": "text on light"},
    "gradients":  [{"name": "signature", "stops": ["#0055EE", "#0071F3", "#0086FF"], "angle": 135}]
  },
  "fonts": {
    "heading":  {"family": "DM Sans", "weight": 800, "file": "fonts/DMSans.ttf"},
    "body":     {"family": "DM Sans", "weight": 500},
    "captions": {"family": "DM Sans", "weight": 800}
  },
  "logos": [
    {"file": "logos/mark.svg", "variant": "primary", "background": "light", "min_height_px": 64,
     "clear_space": "half the mark height", "use": "cards, end card"},
    {"file": "logos/mark_white.png", "variant": "reversed", "background": "dark", "use": "logo bug over footage"}
  ],
  "captions": {"mode": "highlight", "color": "#FFFFFF", "highlight": "#4DA8FF", "outline_color": "#000000",
               "case": "natural", "size": 88},
  "elements": {"corner_radius": 36, "card_shadow": "0 18px 50px rgba(0,0,0,.35)", "stroke": 2},
  "motion": {"feel": "snappy, friendly", "pop": "back.out(1.6)", "entrance": "0.25-0.45 s"},
  "voice": "Friendly, plain-spoken, a little playful.",
  "imagery": "Warm natural light; real people, no stock-photo poses.",
  "rules": {"do": ["White mark on photos"], "dont": ["Recolour or stretch the mark", "More than 2 accents at once"]}
}
```

- **Colours**: any role names; `primary`, `background` and `text` are used by default. Hex only.
- **Fonts**: `file` is optional — `build` finds a matching file in `edit/brand/fonts/`, else downloads
  it from Google Fonts (and warns, so you confirm it at the check-in). Roles beyond heading/body
  (`captions`, `numbers`, `accent`) are fine.
- **Logos**: paths are relative to `edit/brand/` (or absolute); `build` copies them into
  `edit/brand/logos/` and rasterizes SVGs.
- **Captions**: defaults for every edit — mode `highlight` | `plain` | `box`, colours, `case`
  (`upper` | `natural` | `title` | `lower`), `size` (px on the output canvas), `weight`, `font_role`.
- **platform**: the primary delivery format (`reels`, `tiktok`, `shorts`, `vertical`, `youtube`,
  `square`, `feed45`) — sets the safe zone in DESIGN.md. `python helpers/platforms.py` lists them.

## 4. Build and review

```bash
uv run --project <skill_dir> <skill_dir>/helpers/brand_kit.py build --edit-dir <project>/edit --platform reels --frame <project>/edit/still.jpg
```

(`ffmpeg -ss 3 -i <video> -frames:v 1 -vf scale=1080:-2 edit/still.jpg` makes the still.)

It writes:

- `brand.css` — `@font-face` for every font file + CSS variables (`--brand-<role>`,
  `--brand-gradient-<name>`, `--font-<role>`, `--font-<role>-weight`, element tokens). Graphics use
  only these.
- `DESIGN.md` — the binding design system for graphic sub-agents: canvas and safe zone, palette table
  with the readable text colour for each swatch, type and the font-load assertion, logo rules, motion,
  captions, voice, do/don't. Append project specifics at the bottom (face position, layouts used).
- `board.png` — palette, gradients, type specimens in the real fonts, logos on light and dark, and a
  caption preview over a real frame. **This is what you show at the check-in.**
- `fonts/static/` — the static caption font, uniquely named, verified to be the face libass picks.
  `brand.json` gains `captions.ass_fontname`, `fonts_dir`, `font_file`.

Read every `WARNING` and fix every `PROBLEM` before the check-in. Contrast below 4.5:1 for text pairs
is a warning worth mentioning; a font that isn't the brand's is a question for the user.

## Reuse

Brand kits are per project but portable: copy `edit/brand/` into a new project's `edit/` to reuse a
client's kit, then run `build` again for the new platform.
