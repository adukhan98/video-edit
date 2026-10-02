# Motion graphics

Graphics are **transparent overlay videos** composited over the cut by `render.py`. Each one is a
*slot*: one HyperFrames project, built by one sub-agent, rendered to ProRes 4444 with alpha, checked
over the real footage before it goes in. Slots are independent, so build them all in parallel.

## Workflow

1. **Render the cut first** — graphics are timed against it:
   `render.py edl.json -o edit/base.mp4 --base-only` (writes `timeline.json` too).
2. **Plan slots** in `edit/animations/slots.json`, using output-timeline times (read them off
   `takes_packed.md` + `timeline.json`, or a `timeline_view` of `base.mp4`):

   ```json
   [
     {"id": "s1_hook", "start": 0.0, "duration": 3.2, "layout": "top",
      "brief": "Logo mark pops, then 'Muse is everywhere.' — 'everywhere' lands on 'Instagram' (~2.2 s) in primary."},
     {"id": "s2_list", "start": 13.75, "duration": 7.2, "layout": "split", "crop_y": 340,
      "brief": "Checklist panel, 3 rows appearing on 'easy' (0.9 s), 'familiar' (2.6 s), 'WhatsApp' (4.8 s)."}
   ]
   ```

   Write each brief like a director: what appears, in what order, landing on which words, which
   colours/logo variant, what it must not cover. Include exact copy — sub-agents never invent claims.
3. **Prepare**: `slots.py prepare --edit-dir edit` creates per slot `TASK.md`, `slot.json`,
   `words.txt` (words in slot seconds), `refs/` (frames underneath), and `hf/` — a HyperFrames project
   already set to the canvas, the slot duration and a transparent background, with the brand kit
   installed. It also writes `animations/SLOT_BRIEF.md` (shared rules) and prints one prompt per slot.
4. **Spawn one sub-agent per slot in a single message** with the printed prompt. Each authors
   `hf/index.html`, renders `render.mov`, runs `slots.py check`, fixes, and reports.
5. **Review** every `check_sheet.jpg` yourself, then add the slots to the EDL `overlays` (with
   `"layout": "split"` and `crop_y` for split slots) and render a preview.

## Layouts and zones

Zones are computed from the platform safe zone (`platforms.py`) — `TASK.md` states the exact pixels.

| Layout | Zone (vertical 1080x1920, Reels) | Notes |
|---|---|---|
| `full` | the whole safe zone | Backgrounds may bleed to the edges; keep the caption band quiet. Covers the speaker — use for openers, end cards, big moments. |
| `top` | top ~30% of the safe zone (y 285–643) | The face stays visible below. Hook titles, callouts. |
| `lower_third` | just above the caption band | Name/role straps. In, hold, out. |
| `split` | opaque panel on the top half, content inside it | The compositor moves the speaker (`crop_y` = top row of the face crop) into the bottom half while the panel is fully opaque (`swap_inset`, 0.15 s after it lands / before it leaves); captions jump to the seam. |
| `corner` | top-right corner of the safe zone | Small marks. A static logo bug is easier as a PNG overlay in the EDL. |

Landscape (`youtube`) uses the same layouts: `top` becomes a top band, `lower_third` sits above the
caption band, `split` puts the panel on the top half.

## The graphic vocabulary

Pick per video — not every edit needs all of these. Fewer, better graphics beat a busy screen.

- **Hook title** (first 1–3 s): the promise of the video in ≤ 6 words, logo optional. Lands on the
  key spoken word. Kinetic type (word-by-word reveals, a highlighted keyword in the primary colour).
- **Lower third**: name + role/handle the first time a person appears; ≥ 3 s on screen.
- **Keyword / stat callout**: the number or phrase the speaker stresses, big; count-ups for numbers
  (land the final value on the spoken number). Source every stat in NOTES.md / project.md.
- **List / checklist / comparison panel** (split): items appear one by one on their words.
- **Quote / testimonial card**: short pull-quote in the heading font, attribution in body.
- **Logo sting**: ≤ 1.5 s, mark animates in (scale/rotate/draw), wordmark follows. Opener or bumper.
- **End card + CTA** (over `end_hold` or the final seconds): logo, handle/URL, one clear ask. Keep it
  on screen ≥ 2 s; duck or fade the music under it.
- **Progress bar / chapter markers** for tutorials; **icon pops** for lists; **chat bubbles / UI
  mocks** when the story is about a product — built from brand tokens, not screenshots of other
  brands.
- **Transitions**: a brand-colour wipe between sections is a full-layout slot of ~0.6 s; use sparingly.

Non-slot tools in the EDL: `zooms` (punch-ins, hard cuts to a 1.1–1.2× framing — energy on static
stretches), PNG overlays (logo bug, badges), b-roll overlays (`fit: cover`), `end_hold`.

## Timing rules

- **Land on the word**: start the reveal ~0.15 s before the word's time in `words.txt` so the landing
  frame coincides with the spoken word. Without this, graphics feel disconnected.
- **One new thing at a time.** The eye can't track two new elements; stagger 0.1–0.2 s minimum, more
  for separate ideas.
- **Readable at 1×**: sync-to-narration cards 3–7 s (complex diagrams 8–14 s); beat-synced accents
  0.5–2 s. Hold the final state ≥ 1 s before the exit.
- **Over speech**: a graphic's duration ≥ the narration it illustrates + ~1 s.
- **Easing**: ease out on entrances (`power3.out`, `back.out(1.6)` for pops), `power2.inOut` for moves,
  `power2.in` exits. Never linear.
- **First and last frame fully transparent**: the exit must finish *before* the last frame
  (duration − 1/fps) — `slots.py check` fails otherwise.

## HyperFrames notes

- Composition contract: `#root` carries `data-composition-id`, `data-start`, `data-duration`,
  `data-width`, `data-height`; timed elements carry `class="clip"` plus `data-start`,
  `data-duration`, `data-track-index`; the GSAP timeline is created `paused: true` and registered
  as `window.__timelines["main"]`. Everything must be seek-safe and deterministic.
- If the `hyperframes`, `hyperframes-core`, `hyperframes-animation`, `hyperframes-keyframes` or
  `hyperframes-registry` skills are installed, slot sub-agents can load them for deeper patterns
  (text effects, registry blocks for charts, glitch, confetti…). Registry blocks must still be
  restyled with brand tokens.
- `npx --yes hyperframes lint .` before rendering; a nested-structure warning is fine for overlay slots.
- `npx --yes hyperframes render . --format mov -o ../render.mov` → ProRes 4444, `yuva444p12le`, alpha.
  `--quality draft` for quick looks. `--format webm` (VP9 alpha) also works; render.py decodes it with
  libvpx so the alpha survives.
- `hyperframes init` refuses a non-empty folder — that's why the project lives in `<slot>/hf/`.
  `prepare` handles it; if you scaffold by hand: `cd <slot> && HYPERFRAMES_SKIP_SKILLS=1 npx --yes
  hyperframes init hf --example blank --non-interactive --resolution portrait`, then set the duration
  and a transparent `html, body, #root` background, then `brand_kit.py install hf --edit-dir <edit>`.
- Graphics that must sit *between* shots (an opaque intro card, a full-screen title with its own
  sound) can instead be rendered as opaque MP4 (`--format mp4`) and placed as a **range** in the EDL
  with `"grade": "none"`; render.py adds silence if the file has no audio.

## Colour accuracy

render.py composites overlays and captions in RGB and converts to BT.709 once, so brand hex values
survive (measured within a few levels of the source hex after H.264). HyperFrames' ProRes output
is untagged and decodes correctly with ffmpeg's defaults. Don't "fix" colours by eye in a slot —
if a colour looks off, check the hex in DESIGN.md first.

## QA checklist (per slot)

`slots.py check <slot_dir>` verifies size, fps, duration (±1 frame), alpha, transparent first/last
frame and safe-zone spill, then writes `check_sheet.jpg` — 8 frames of the render over the real cut,
safe zone in red, caption band in yellow. Then look:

- Everything inside the red box; the caption band quiet while captions show.
- The brand font is actually rendering (compare letterforms with `board.png`).
- Logo crisp, correct variant for its background, untouched.
- Text legible at phone size (≥ 30 px at 1080 wide; headlines 64–110 px).
- Reveals line up with their words (pull single frames at the word times if unsure).
- It doesn't cover the face (except `full`/`split` by design) or fight the b-roll underneath.
