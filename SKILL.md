---
name: video-edit
description: Turn raw footage plus a brand's documents into a finished, on-brand edit with motion graphics. Use when someone drops in videos together with brand material (guidelines PDF, logos, fonts, colour docs, a style guide) and wants them cut, captioned and dressed with branded graphics — reels/shorts/TikToks, ads, launch and promo videos, talking heads, tutorials, interviews, podcasts. Builds a brand kit from the docs, transcribes, cuts on word boundaries, grades, adds branded captions, titles, lower thirds, callouts, split-screens, logo stings, end cards, b-roll and music, self-checks, renders. Downloads any video or audio it needs with yt-dlp.
---

# Video Edit

People drop in **brand documents + raw footage**; you hand back an edited video whose every graphic,
caption and colour is on-brand. You never watch the video — you **read** it (a word-level transcript
plus filmstrip/waveform images at decision points) and you **read the brand** (its documents, logos
and fonts, distilled into one brand kit every graphic is built from).

## Principles

1. **The brand kit is the source of truth for look; the transcript is the source of truth for timing.**
   Graphics take colours, type, logos and motion from `brand.json` → `DESIGN.md`; every cut, reveal
   and caption is placed from word timestamps.
2. **One check-in, then execute.** Read the brand docs and the footage, then show ONE combined
   check-in — the brand board plus the edit plan — and wait for an OK before rendering anything. If
   the request says "autopilot" / "no questions" / "just do it", skip the wait, choose sensibly, and
   list the assumptions you made when you deliver.
3. **Audio is primary, visuals follow.** Cut candidates come from speech boundaries and silence gaps.
   Drill into visuals only at decision points.
4. **Generalize.** Don't assume what kind of video it is. Look at the material, then edit.
5. **Artistic freedom inside the brand.** Every value in this file is a worked example, not a mandate —
   except the Hard Rules. The brand constrains palette, type and logo use; within that, invent:
   split-screens, kinetic type, speed ramps, freeze frames, match cuts, J/L-cuts, whatever the
   material wants. Don't wait for permission.
6. **Verify your own output before showing it.** If you wouldn't ship it, don't present it.

## Hard Rules (production correctness — non-negotiable)

Deviating from these produces silent failures or broken output. They are correctness, not taste.

1. **Captions are applied LAST**, after every overlay, or graphics hide them. (`render.py` does this.)
2. **Per-segment extract → lossless `-c copy` concat**, never one giant filtergraph for the cut.
3. **30 ms audio fades at every segment boundary**, or every cut pops. (Built into `render.py`.)
4. **Overlays are PTS-shifted** (`setpts=PTS-STARTPTS+T/TB`) so frame 0 lands at the window start.
5. **Captions use output-timeline offsets** — from the measured `timeline.json`, not nominal
   durations (each segment rounds to whole frames; over dozens of cuts nominal offsets drift).
6. **Never cut inside a word.** Snap every edge to a word boundary from the transcript.
7. **Pad every cut edge** (working window 30–200 ms) to absorb timestamp drift.
8. **Word-level verbatim transcripts only.** Never phrase/SRT-level ASR.
9. **Cache transcripts per source.** Never re-transcribe an unchanged file.
10. **Parallel sub-agents for graphics.** One sub-agent per slot, all spawned at once.
11. **The one check-in happens before any render** (unless the user asked for autopilot).
12. **All session outputs go in `<project>/edit/`.** Never write inside the skill directory.
13. **Brand fidelity.** Use only the supplied logo files — never redraw, recolour, stretch or re-letter
    a logo. Use the exact hex values from the brand kit. Fonts come from brand files (or an agreed
    Google Fonts match); a graphic that falls back to another face is a failed deliverable — assert the
    font loaded before rendering.
14. **Caption fonts are static instances.** libass can hang on variable fonts and silently swaps in
    another face when a name doesn't match; `brand_kit.py build` makes a uniquely named static
    instance and verifies libass picks it. Don't point captions at a variable font.
15. **Graphics are transparent, full-canvas renders** at the output size and fps (HyperFrames
    `--format mov` → ProRes 4444 with alpha), first and last frame fully transparent, every element
    inside the platform safe zone. Check each one with `slots.py check` before it goes in the cut.
16. **Downloads go through `fetch.py` (yt-dlp) only**, land in `edit/downloads/`, and are logged with
    source URL and licence. Never put third-party footage or music in a deliverable unless it is
    Creative Commons (CC BY / CC0 — not NC or ND) or the user confirms they hold the rights; ship
    `credits.md` with anything that needs attribution.

## Inputs and layout

Users drop footage and brand material wherever is convenient — a `brand/` folder next to the
footage, files mentioned in chat, or links. Treat as brand material: PDFs, DOCX/PPTX/MD/TXT style
guides, logo files (SVG, PNG, JPG), font files (TTF, OTF, WOFF, WOFF2), screenshots of brand pages,
reference videos (past ads, the look to match), sonic logos, and links to any of these. If they gave
only a website, read it (WebFetch/curl the HTML and CSS for colours, fonts, logo) and say so.

```
<project>/
├── <source videos, untouched>
├── brand/                         ← whatever the user supplied (any name, any location)
└── edit/
    ├── project.md                 ← memory; appended every session
    ├── brand/
    │   ├── scan.json, text/, extracted/   ← brand_kit.py scan output
    │   ├── brand.json             ← the brand kit you write (schema: references/brand.md)
    │   ├── brand.css, DESIGN.md, board.png  ← brand_kit.py build output
    │   ├── fonts/ (+ static/ for captions), logos/
    ├── transcripts/<source>.json  ← cached word-level transcripts
    ├── takes_packed.md            ← phrase-level reading view
    ├── edl.json                   ← cut + graphics + audio decisions
    ├── timeline.json              ← measured segment offsets (render.py)
    ├── base.mp4                   ← the cut without graphics (graphics are built against it)
    ├── animations/
    │   ├── slots.json, SLOT_BRIEF.md
    │   └── <slot_id>/  TASK.md, words.txt, refs/, hf/ (HyperFrames project), render.mov, check_sheet.jpg
    ├── downloads/                 ← yt-dlp output + manifest.json (+ search/ contact sheets)
    ├── captions.ass, credits.md
    ├── verify/                    ← QA frames
    ├── preview.mp4
    └── final.mp4 (+ final_<format>.mp4 per extra delivery format)
```

## Setup

First-time install is in `install.md`. On a cold start just verify, and fix only what's missing:

- `ffmpeg` + `ffprobe` on PATH (with libass, zscale and libvpx — Homebrew's ffmpeg has them).
- Python deps: helpers run as `uv run --project <skill_dir> <skill_dir>/helpers/<name>.py …`
  (uv installs the deps on first run). Without uv: `pip install -e <skill_dir>` and call `python`.
- Transcription: an `ELEVENLABS_API_KEY` (env or `<skill_dir>/.env`) → ElevenLabs Scribe, best quality.
  No key → local whisper.cpp (`whisper-cli`, `brew install whisper-cpp`) with a `ggml-medium.en.bin`
  model. `transcribe.py` finds models in `~/.cache/video-edit/models`, `~/.cache/hyperframes/whisper/models`
  and Homebrew's share dir, and prints the download command if none exists — ask before downloading
  (~1.5 GB). Never write a key into the user's project folder.
- Node.js 22+ for HyperFrames graphics (`npx --yes hyperframes …`, nothing to install up front).
- yt-dlp is resolved by `fetch.py` (system yt-dlp ≥ 2025.11.12, else `uvx` runs the latest one). YouTube
  needs a JS runtime — Deno if installed, else Node 22+ is enabled automatically.

`<skill_dir>` is the directory containing this file (usually `~/.claude/skills/video-edit`). Resolve
helper paths from it; never `cd` into it to write outputs.

## Helpers

All take `--help`. Paths below are relative to `<skill_dir>/helpers/`.

| Helper | What it does |
|---|---|
| `brand_kit.py scan <paths…> --edit-dir E` | Inventory brand files: PDF/DOCX/PPTX text → `brand/text/`, embedded images, SVG→PNG, font names, colour candidates with context, ranked logo candidates. |
| `brand_kit.py build --edit-dir E [--platform reels] [--frame still.jpg]` | Validate `brand.json`; fetch missing Google Fonts; make + verify the static caption font; write `brand.css`, `DESIGN.md`, `board.png`; contrast warnings. |
| `brand_kit.py install <dir> --edit-dir E` | Copy brand.css + fonts + logos into a graphics project. |
| `brand_kit.py fonts / pdf / svg / alpha` | Google Font → static TTFs; render PDF pages or lift a vector logo (`--crop … --transparent`); SVG → PNG; knock out a logo's solid background. |
| `transcribe.py <video>` / `transcribe_batch.py <dir>` | Word-level transcript (Scribe or local whisper; `--vocab "Brand, Product"` fixes spellings). Cached. |
| `pack_transcripts.py --edit-dir E` | `transcripts/*.json` → `takes_packed.md` (phrases, break on ≥ 0.5 s silence). |
| `timeline_view.py <video> <start> <end>` | Filmstrip + waveform PNG for a range. A drill-down at decision points, not a scan tool. |
| `audio_env.py snap <video> <t…>` | Nearest quiet 10 ms to a cut edge (useful with local transcripts). |
| `audio_env.py islands <video>` / `tighten <video> a-b c-d …` | Where speech really is (whisper hides long pauses inside words); split kept spans at every internal pause ≥ 160 ms → tight jump-cut ranges as JSON. |
| `verify_cuts.py <edl>` / `--source v --probe a:b,c:d` | Transcribe the audio across every join (or a candidate join) — proves a filler cut didn't clip or leave half a word. |
| `headpose.py --edl <edl>` / `<video>` | macOS Vision head pitch per frame: flags range edges where the speaker looks down at notes or is still lifting their head. |
| `render.py <edl> -o out.mp4 [--captions] [--preview/--draft] [--base-only] [--reuse-base]` | Extract → concat → composite (zooms, split layouts, overlays, captions LAST) → loudness. Prints the measured LUFS / true peak. |
| `captions.py --edl edl.json [--verify-font]` | Build the branded `captions.ass` alone (render.py `--captions` does it for you). |
| `slots.py prepare --edit-dir E` / `slots.py check <slot_dir>` | Set up graphic slots for parallel sub-agents; QA a slot render over the real cut (safe zone, caption band, alpha, timing). |
| `fetch.py url / search / credits` | yt-dlp downloads (cached, licence-logged), Creative Commons b-roll search with verified licences and a thumbnail sheet, `credits.md`. |
| `grade.py` | ffmpeg grade presets and auto-grade (used per segment by render.py). |
| `platforms.py` | Prints canvas, safe zone and caption band per delivery format. |

## The process

1. **Resume.** If `edit/project.md` exists, read it and summarize the last session in one sentence
   before asking whether to continue.
2. **Intake.** Find the footage and the brand material (folders, files named in chat, links). Pull
   linked media with `fetch.py url` (it's the user's link: rights are theirs).
3. **Brand.** `brand_kit.py scan` → read the guidelines yourself (Read tool on the PDF — look at the
   pages, don't trust text extraction for colours shown only as swatches) and look at the logo
   candidates → write `edit/brand/brand.json` (schema and rules in `references/brand.md`) →
   `brand_kit.py build --platform <primary format> --frame <a still from the footage>`. Fix every
   PROBLEM it prints. Missing pieces (no fonts, no colours, contradictions) become questions for the
   check-in, each with your proposed default.
4. **Footage.** `ffprobe` every source (orientation, fps, HDR, audio tracks). `transcribe_batch.py`
   (pass `--vocab` with brand/product names) → `pack_transcripts.py`. Look at one or two
   `timeline_view`s — is it a front-camera selfie (text reads backwards → `"mirror": true`)? Where is
   the face (for crops, split layouts, safe placement)? With local whisper, run `audio_env.py islands`
   too: a single word lasting seconds in the transcript (`models.` 25.9–32.8) is a pause whisper
   swallowed — the islands are the truth, and the real runtime is usually much shorter than the
   phrase list suggests. Talking-head sources: `headpose.py <video>` shows where the speaker reads notes.
5. **Pre-scan.** One pass over `takes_packed.md` for slips, false starts, repeats, the lines that
   carry the message (those become graphic beats), and **fillers to cut**: throat-clearers ("Great.",
   "So,", "Basically", "Roughly"), sentence-start "So"s, and trailing clauses that restate the point
   ("…and make sure that they work"). Propose the cut list at the check-in; for short-form, default
   to cutting them.
6. **The check-in** (one message): show `board.png` (attach it, or give its path) and summarize
   how you read the brand; then the plan in plain English — structure and target length, take
   choices, what gets cut, the graphics list (each: when, what, which layout), caption style, b-roll
   needs (user footage first, then CC search), music/SFX, grade, delivery formats. Ask only the
   questions the material and the brand raise. **Wait for OK** (unless autopilot).
   **Caption style is a taste call — show it, don't describe it:** render 3–4 styles on a real frame
   (e.g. bold caps with stroke · clean sentence-case with soft shadow · lowercase grotesk · box
   highlight) into one grid image and let the user pick. Don't default captions to the brand's
   condensed display face (Anton-type fonts read as cramped at caption size).
7. **Cut.** Write `edl.json` (format below; for multi-take selection use the editor sub-agent brief).
   Short-form talking heads: cut **tight** — `audio_env.py tighten` removes every internal pause
   ≥ 160 ms (jump cuts are the style; alternate punch-in `zooms` across them). Filler cuts: find the
   word boundary on the envelope (`audio_env.py profile`), then **prove it**: `verify_cuts.py --probe`
   candidate points until the joined audio transcribes to exactly the words you meant to keep.
   `render.py edl.json -o edit/base.mp4 --base-only` (add `--preview` for speed), then
   `verify_cuts.py edl.json` and `headpose.py --edl edl.json`: trim any flagged head-move edge when
   no words are lost, otherwise plan a full-screen graphic or b-roll over it. Drill into
   `timeline_view` at ambiguous edges.
8. **Graphics.** Write `animations/slots.json` against the base's timeline, `slots.py prepare`, then
   spawn **one sub-agent per slot, all in the same message**, each with the one-line prompt prepare
   prints. They build in HyperFrames, render ProRes 4444 with alpha and self-QA with `slots.py check`.
   Look at every `check_sheet.jpg` yourself when they report back. Details: `references/motion-graphics.md`.
   Before spawning: when the transcript is loose (local whisper), overwrite each slot's `words.txt`
   with verified times — agents land reveals on those numbers. Several slots that must look like one
   system get a shared art-direction file (`animations/SHARED.md`) every brief points to. After the
   renders: set each overlay's EDL `duration` to the render's real length (`ffprobe`) — renders round
   to whole frames. An agent killed mid-task (rate limit) keeps its context: resume it with
   SendMessage instead of respawning.
9. **B-roll, music, SFX.** `fetch.py search` for CC b-roll when the user has none (look at the
   contact sheet, pick, `fetch.py url --section a-b --found-by-search`). Music: the user's tracks
   or links first; CC tracks second. Details: `references/downloads.md`.
10. **Preview.** Add overlays / zooms / audio / captions to the EDL →
    `render.py edl.json -o edit/preview.mp4 --preview --captions --reuse-base`.
11. **Self-eval before showing anyone** (see below). Fix → re-render → re-eval, max 3 passes; then
    flag what's left rather than loop.
12. **Iterate + deliver.** Apply feedback, re-render (`--reuse-base` when the cut didn't change).
    Final: `render.py edl.json -o edit/final.mp4 --captions`. Extra formats are separate passes with
    their own canvas and their own graphic renders. `fetch.py credits` before delivering. Append to
    `project.md`.

## Self-eval (before the user sees anything)

- `timeline_view` on the **rendered output** at every cut boundary (±1.5 s): visual jumps, flashes,
  waveform spikes (pops), captions hidden behind graphics, overlays showing the wrong frames.
- Sample first 2 s, last 2 s and 2–3 midpoints at full size: grade consistency, caption legibility at
  phone size, brand colours and fonts actually on screen, logos crisp and unaltered.
- Every graphic: `slots.py check` passed, and its window lines up with the words it illustrates.
- Safe zones: nothing important under the platform UI (check a frame per graphic against `platforms.py`).
- Footage sanity: mirrored front-camera footage flipped; long static stretches (> ~8 s with nothing
  changing) get a punch-in, a graphic or b-roll; the ending isn't the speaker reaching for the
  phone — trim it or `end_hold` a clean frame under the end card.
- Talking heads: `headpose.py --edl` shows no flagged edge left uncovered (viewers notice a head
  dropping to notes at a cut long before they notice an audio seam); no pause ≥ 0.2 s left inside a
  line; `verify_cuts.py` reads as the intended script at every join.
- Captions: sample a frame just after each split panel enters and exits — a cue that started before
  the layout change must not sit at the wrong height.
- Audio, measured: `render.py` prints integrated LUFS and true peak — expect about −14 LUFS and below
  −1 dBTP. Check section levels with `ffmpeg -i out.mp4 -af ebur128=peak=true -f null -`; an end card
  15 dB under the dialogue, or effects louder than speech, is a bug. You can't listen: report numbers.
- `ffprobe` the output: duration, size, fps as planned.
- Anything publishable (ad, launch, promo): spawn one **critic sub-agent** with the render, the EDL,
  `DESIGN.md` and any reference videos. Brief it to roast, not praise: a verdict, ranked problems with
  timecodes and evidence, the 5 fixes to do first — brand violations included.

## Cut craft

- **Audio-first.** Candidate cuts from word boundaries and silence gaps. Silences ≥ 400 ms are the
  cleanest; 150–400 ms usable with a visual check; < 150 ms is mid-phrase.
- **Preserve peaks** — laughs, punchlines, emphasis. Extend past them; the reaction is the beat.
- **Speaker handoffs** want 400–600 ms of air (less for fast, more for cinematic).
- **Example padding** (a shipped launch video): 50 ms before the first kept word, 80 ms after the last.
- **Local transcripts** (whisper) are a little less exact than Scribe: `audio_env.py snap` an edge onto
  the quietest nearby frame when a cut feels tight, and re-check edges in `timeline_view`.
  whisper.cpp also (a) swallows long pauses into one word, (b) starts words 0.2–0.4 s late after a
  pause, and (c) can skip tens of seconds of a long continuous file. For caption timing on a finished
  cut, transcribe **each segment separately** (short clips align well) and fix the text against the
  known script.
- **Fillers are cut on the envelope, not the timestamps.** A filler glued to the next word ("So the
  next", "hardware basically all") has no silence; find the 20 ms dip, try 2–4 candidate points with
  `verify_cuts.py --probe`, keep the one that transcribes clean. Expect 2–3 rounds.
- **Head moves are cut edges too.** Speakers who read notes drop their head ~0.2 s after the last word
  and lift it ~0.5 s before the next line: end tight (≤ 30 ms pad) and start on the word.
- **Never reason audio and video independently.** Every cut must work on both tracks.

## The packed transcript

`takes_packed.md` lists each take as phrase lines with `[start-end]` times — word-boundary precision
from text alone, at a tenth of the tokens of raw JSON:

```
## IMG_2298  (duration: 5.9s, 1 phrases)
  [001.54-007.40] S0 If you're wondering why Muse is all over your Instagram, I think Meta is making a very, very smart bet here.
```

## Editor sub-agent brief (multi-take selection)

When the task is "pick the best take of each beat across many clips," spawn a dedicated sub-agent:

```
You are editing a <type> video for <brand>. Pick the best take of each beat and assemble them
chronologically by beat, not by source clip order.

INPUTS:
  - takes_packed.md (time-annotated phrase-level transcripts of all takes)
  - Context: <2 sentences from the user> · Brand voice: <from brand.json>
  - Speaker(s): <name, role, delivery note>
  - Structure: <archetype or invented>   · Slips to avoid: <pre-scan list>   · Target runtime: <s>

Archetypes (pick, adapt or invent):
  Ad / promo:      HOOK → PROBLEM → PRODUCT → PROOF → CTA
  Launch / demo:   HOOK → PROBLEM → SOLUTION → BENEFIT → EXAMPLE → CTA
  Creator reel:    HOOK → THESIS → 2–3 POINTS → PAYOFF → CTA
  Tutorial:        INTRO → SETUP → STEPS → GOTCHAS → RECAP
  Interview:       (QUESTION → ANSWER → FOLLOW-UP) repeat
  Testimonial:     WHO → BEFORE → AFTER → RECOMMENDATION

RULES: start/end on word boundaries; pad 30–200 ms; prefer silences ≥ 400 ms as cut points; keep an
unavoidable slip only when no better take exists (say so in "reason"); if over budget, drop a beat
or trim tails and re-check the total.

OUTPUT (JSON array, no prose):
  [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK", "quote": "...", "reason": "..."}]
Then one line: total runtime.
```

## Brand kit (summary — full rules in `references/brand.md`)

- Extract: palette with roles (primary, secondary, accent, background, text, gradients), type
  (heading/body families and weights; caption font and weight), logo variants (primary / reversed /
  mark-only / wordmark, which background each is for, minimum size, clear space, misuse rules),
  element styles (radius, shadows, strokes), motion notes, voice, do/don't lists, imagery style.
- Precedence when sources disagree: written guidelines > logo files > website > your inference. Note
  conflicts and ask at the check-in.
- Fonts: brand-supplied files win. A commercial font that wasn't supplied can't be downloaded — ask
  for the files or propose the closest Google Font and get a yes at the check-in.
- No brand docs at all? Derive a minimal kit from the logo (its colours), propose type, and confirm.

## Motion graphics (summary — full guide in `references/motion-graphics.md`)

Engine: **HyperFrames** (HTML + CSS + GSAP, rendered frame-accurately, transparent ProRes 4444).
Brand tokens arrive as CSS variables from `brand.css`, logos as files, fonts as `@font-face` — so the
graphics match the brand by construction. Typical vocabulary, chosen per video:

| Graphic | Layout | Use |
|---|---|---|
| Hook title / kinetic headline | top or full | first 1–3 s; land on the key spoken word |
| Lower third | lower_third | name + role the first time someone speaks |
| Keyword / stat callout | top | the number or phrase the speaker stresses |
| Split-screen explainer panel | split | a list, comparison or diagram while the speaker continues below |
| Logo bug | corner (PNG overlay in the EDL) | brand presence through the middle |
| Logo sting / intro | full | opener or between sections; keep ≤ 1.5 s |
| End card + CTA | full, over `end_hold` | last 2–4 s; brand, handle/URL, the ask |
| B-roll cutaway | overlay (video, `fit: cover`) | illustrate what's being said |
| Punch-in zoom | EDL `zooms` | energy on static stretches, emphasis |

Timing rules: land each reveal on its word (start ~0.15 s before the timestamp); one new element at
a time; hold the final state ≥ 1 s; sync-to-narration cards 3–7 s, beat accents 0.5–2 s; ease out,
never linear. A graphic should be driven by the same data as the sound — word times from
`words.txt`, never eyeballed.

**Show the real thing, not a number in a box.** When the video is about a concrete object — money,
a product, a chip, a phone — make that object the hero: real photographs or scans (public-domain
or CC, through `fetch.py`), given physical motion (3D tilt, flutter, contact shadows, motion blur
while fast). Numbers become labels on it. Feedback from a shipped reel: four identical
"big number + icon" panels read as a template; real banknotes being broken into change, zapped and
shredded read as an edit. Other levers that worked:
- **A continuity object** carried across panels (a wallet draining $100 → $50 → $30 → $15 → $0)
  turns separate beats into one story.
- **Vary the layout**: top cards while the speaker is engaging, split panels for explanations,
  full-screen for big moments, so no two neighbouring beats look the same.
- **Full-screen graphics double as cover** for unusable footage (a head lifting from notes, a cut
  that jumps too hard).
- **A pay-off visual for the scale-up line** ("billions") — zoom out to an endless grid of the
  thing.

## Captions

## Captions

`render.py --captions` builds `captions.ass` from the brand kit: brand font (static instance), text and
active-word colours, outline, case, mode (`highlight` / `plain` / `box`), on the platform's caption
band. Tune per project in the EDL `captions` block (brand.json `captions` is the default):
`words_per_cue`, `max_words`, `max_chars`, `case`, `y`, `windows` (position overrides), `fixes`
(`{"Mehta": "Meta"}` — always fix brand names), `censor`. During split layouts captions move to the
seam automatically. Proofread captions against the audio: whisper sometimes expands contractions.

Short-form styling options (all opt-in, in the EDL `captions` block):
- `"reveal": true` — words appear as they are spoken (later words hidden, space reserved).
- `"active_pop": 12` — the spoken word pops 12 % and eases back in 110 ms.
- `"emphasis": {"$50": "#00D26A"}` — always coloured, scaled by `emphasis_scale` (default 112 %).
  A list `[{"word": "$15", "color": "#4CC9F0", "start": 0, "end": 30}, …]` lets a word change
  colour by section, e.g. matching each graphic's category colour.
- `"soft_shadow": true` — no hard stroke, a blurred shadow layer underneath. This is the clean
  sentence-case look; pair it with a sturdy sans (Inter / Poppins ExtraBold, `"case": "natural"`).

A reel's caption that worked after feedback: Inter ExtraBold 92 px (1080×1920), natural case,
`reveal` + `active_pop` + `emphasis` + `soft_shadow`, 1–3 words per cue, ≤ 16 characters. Font
files: `brand_kit.py fonts "Inter" --weights 800 -o edit/capfonts`, then point `fonts_dir` at that
folder.

## Color grade

Reason about the image, not a preset: look at a frame, decide what's wrong, adjust one thing, look
again. `grade` in the EDL takes `auto` (subtle per-segment correction), a preset (`neutral_punch`,
`warm_cinematic`, `subtle`, `none`) or a raw ffmpeg filter; a range can override it (`"grade": "none"`
for graphics inserts). The brand may imply a look (warm, clinical, punchy) — follow it, never at the
expense of skin tones. HDR (HLG/PQ) sources are tone-mapped to Rec.709 automatically.

## Music and sound

- Fewer effects. Every effect is tied to something visible (a cut, a landing, a reveal); ~8 in 18 s
  reads as designed, ~20 as generic.
- Hit on the frame: measure an effect's attack and start it that much before the visible contact.
- Music beds use `"duck": true` (sidechain-compressed ~10–15 dB under speech, measured). Fade out
  before an end card instead of letting the tail decay under the CTA.
- The user's music first; a brand's sonic logo if supplied; CC tracks via `fetch.py` otherwise. Offer
  two contrasting beds — music taste is the user's call. You can only measure a mix, not hear it.

## Output formats

Match the source unless the plan says otherwise. Set `output` in the EDL so graphics and base share an
exact canvas: `1080x1920@30` vertical (Reels/TikTok/Shorts), `1920x1080` landscape, `1080x1080`
square, `1080x1350` 4:5 feed. `fit: crop` with `focus_x/focus_y` reframes landscape footage for
vertical (per range when the subject moves); `fit: pad` letterboxes. Each extra format is a separate
pass: own canvas, own safe zone, own graphic renders.

## EDL format

```json
{
  "version": 2,
  "sources": {"C0103": "/abs/path/C0103.MP4", "card_end": "animations/end_card/render_opaque.mp4"},
  "output": {"width": 1080, "height": 1920, "fps": 30, "fit": "crop", "focus_x": 0.5, "focus_y": 0.4},
  "mirror": false,
  "ranges": [
    {"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK", "quote": "...", "reason": "...",
     "focus_x": 0.42},
    {"source": "C0108", "start": 14.30, "end": 28.90, "beat": "PROOF", "mirror": true}
  ],
  "grade": "auto",
  "zooms": [{"start": 7.7, "end": 10.8, "scale": 1.15, "x": 0.5, "y": 0.36}],
  "overlays": [
    {"file": "animations/s1_hook/render.mov", "start_in_output": 0.0, "duration": 3.2},
    {"file": "animations/s2_list/render.mov", "start_in_output": 13.75, "duration": 7.2,
     "layout": "split", "crop_y": 340},
    {"file": "downloads/skyline-abc123_2-5.mp4", "start_in_output": 21.0, "duration": 2.2, "fit": "cover"},
    {"file": "brand/logos/logo_white.png", "start_in_output": 3.2, "duration": 60, "x": 850, "y": 300,
     "width": 110, "opacity": 0.9}
  ],
  "captions": {"mode": "highlight", "fixes": {"Mehta": "Meta"}, "windows": []},
  "audio": [
    {"file": "downloads/bed-xyz_10-70.opus", "start_in_output": 0, "gain_db": -14, "duck": true,
     "fade_in": 0.5, "fade_out": 1.5},
    {"file": "sfx/pop.wav", "start_in_output": 2.04, "gain_db": -10}
  ],
  "end_hold": 1.2,
  "total_duration_s": 87.4
}
```

- Paths are absolute or relative to the EDL's folder (`edit/`).
- `ranges[]`: per-range `mirror`, `focus_x/focus_y`, `fit`, `grade`, `audio_track` override the top
  level. A source with no audio (an opaque graphics insert) gets silence automatically.
- `overlays[]`: `.mov` ProRes 4444 / `.webm` VP9 alpha / `.png` / any video. Numbers in `x`, `y`,
  `width`, `height` are canvas pixels; strings are ffmpeg expressions. `hold` freezes the last frame;
  `trim_start` skips into a video; `fit` (`cover` default, `contain`, `stretch`) for b-roll;
  `layout: "split"` + `crop_y` moves the speaker into the bottom half while the panel is opaque.
- `audio[]`: `trim_start`, `duration`, `gain_db`, `fade_in`, `fade_out`, `duck`.
- `end_hold` freezes the last frame (audio padded with silence) — put the end card over it.

## Memory — `project.md`

Append one section per session:

```markdown
## Session N — YYYY-MM-DD
**Brand:** source docs, key tokens, open brand questions
**Strategy:** one paragraph
**Decisions:** takes, cuts, graphics (slot list), captions, grade, audio + why
**Reasoning log:** one line per non-obvious call
**Outstanding:** deferred items, rights still to confirm
```

## Anti-patterns

- **Inventing brand assets.** Redrawn logos, "close enough" colours, a guessed font. Use the kit or ask.
- **Trusting whisper's pauses.** A transcript phrase list hides seconds of dead air inside single
  words; the cut looks tight on paper and plays slow. Measure with `audio_env.py islands`/`tighten`.
- **Unverified filler cuts.** A cut a few hundred ms off leaves "hardware-ba…" or eats "the next".
  `verify_cuts.py` every join.
- **Cutting on audio while the head is still moving.** Check `headpose.py --edl`.
- **Abstract-only graphics for a concrete topic.** Numbers on boxes, when the story is about money
  (or a product) you could show for real.
- **Graphics without the brand kit.** Every slot imports `brand.css`; no hard-coded colours or fonts.
- **Unverified fonts.** Web fonts that fail fall back silently; libass swaps faces silently. Assert / verify.
- **Variable fonts in libass captions.** Hangs or wrong weights. Static instance only.
- **Opaque or mis-sized overlays.** A graphic without alpha blacks out the video; a canvas mismatch misplaces it.
- **Graphics in the platform UI.** Text under the Reels caption/username area or the action rail is invisible.
- **Hierarchical pre-computed "shot databases" and hand-tuned scoring.** Derive from the transcript at decision time.
- **Phrase-level / normalized ASR.** Loses the gaps and fillers you cut on.
- **Burning captions before overlays.** Graphics hide them (Hard Rule 1).
- **One filtergraph for the whole cut.** Double re-encodes (Hard Rule 2).
- **Linear easing, parallel reveals.** Robotic; the eye can't track two new things at once.
- **Stock SFX on every transition.** Tie each effect to a visible event.
- **Third-party media without rights.** CC BY/CC0 or user-owned only; NC/ND licences are not usable in brand work.
- **Sequential graphic sub-agents.** Always parallel.
- **Editing before the check-in.** Never (unless autopilot).
- **Re-transcribing cached sources.** Immutable outputs of immutable inputs.
- **Assuming what kind of video it is.** Look first, ask second, edit last.
