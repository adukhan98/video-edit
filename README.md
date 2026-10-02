# video-edit

**Drop in your brand docs and your raw footage. Get back an on-brand edit with motion graphics.**

A Claude Code skill that reads a brand's guidelines, logos and fonts, turns them into a brand kit, and
edits your videos with it: cuts on word boundaries, branded captions, titles, lower thirds, callouts,
split-screens, logo bugs, end cards, b-roll and music — then checks its own work before you see it.
Talking heads, reels/shorts, ads, launches, tutorials, interviews, podcasts.

## What it does

- **Reads your brand.** PDFs, style guides, logos (SVG/PNG), fonts (TTF/OTF/WOFF2), colour docs →
  `brand.json` → `brand.css` + a binding `DESIGN.md` + a **brand board** you approve before anything
  renders. Missing fonts come from Google Fonts when they exist; commercial fonts it asks you for.
- **Cuts like an editor.** Word-level transcripts (ElevenLabs Scribe, or free local whisper.cpp), filler
  and false-start removal, best-take selection, 30 ms fades at every cut, HDR tone-mapping, reframing
  landscape footage for vertical.
- **Builds motion graphics in parallel.** Each graphic is its own [HyperFrames](https://github.com/heygen-com/hyperframes)
  project, built by its own sub-agent from the brand kit, rendered transparent (ProRes 4444), and
  QA'd over the real footage against the platform's safe zone.
- **Branded captions.** Your font, your colours, active-word highlight, safe-zone placement, moved to
  the seam during split-screens — rendered so brand hex values stay exact.
- **Downloads with yt-dlp.** Links you give it, plus Creative Commons b-roll and music it finds itself —
  licence-verified, cached and credited.
- **Mixes and masters.** Music ducked under speech, sound effects on the frame, −14 LUFS with true peak
  under −1 dBTP, measured and reported.
- **Remembers.** `project.md` lets next week's session pick up where this one stopped.

## Install

Paste into Claude Code:

```text
Install the video-edit skill from https://github.com/adukhan98/video-edit — read install.md and follow it.
```

Or by hand:

```bash
git clone https://github.com/adukhan98/video-edit ~/.claude/skills/video-edit
cd ~/.claude/skills/video-edit && uv sync
brew install ffmpeg whisper-cpp yt-dlp node      # node 22+ for HyperFrames graphics
```

Transcription is local and free by default (whisper.cpp; `install.md` covers the model). For the best
transcripts add an ElevenLabs key: `cp .env.example .env` and set `ELEVENLABS_API_KEY`.

## Use

Put footage and brand files in a folder (a `brand/` subfolder is tidy but not required), open Claude
Code there, and ask:

> Edit these into a 45-second Instagram reel using our brand guidelines in brand/

It scans the brand, transcribes, and comes back once with the brand board and an edit plan. Say OK
(or ask for changes) and it builds the cut, the graphics, captions and audio, checks everything, and
leaves `edit/final.mp4` next to your footage. Say "autopilot" to skip the check-in.

## How it works

The model never watches the video. It **reads** it — a word-level transcript (`takes_packed.md`) plus
filmstrip-and-waveform images at decision points — and it reads the brand through one brand kit that
every graphic and caption is generated from.

```
brand docs ──> scan ──> brand.json ──> brand.css · DESIGN.md · board.png ─┐
footage ─────> transcribe ──> pack ──> plan ──(check-in)──> EDL ──> cut ──┤
                                         graphics slots (parallel) ───────┤
                                         yt-dlp b-roll / music ───────────┤
                                                                          └─> composite ─> loudness ─> self-eval ─> final.mp4
```

All outputs live in `<your folder>/edit/`; the skill folder stays clean. See [`SKILL.md`](./SKILL.md)
for the full production rules and [`references/`](./references) for brand intake, motion graphics and
downloads.

## Requirements

ffmpeg (with libass/zscale/libvpx — Homebrew's build has them), Python 3.10+ with
[uv](https://docs.astral.sh/uv/), Node.js 22+, whisper.cpp or an ElevenLabs key, yt-dlp (or just uv —
it can run the latest yt-dlp on demand).

## License

MIT — see [LICENSE](./LICENSE).
