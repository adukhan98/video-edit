---
name: video-edit-install
description: Install the video-edit skill into Claude Code (or another agent) and wire up ffmpeg, transcription, HyperFrames and yt-dlp so the user can start editing immediately.
---

# video-edit install

Use this file only for first-time install or repair. For editing, read `SKILL.md`.

## What must exist

1. This repo at `~/.claude/skills/video-edit` (Claude Code) — the folder *is* the skill; `helpers/`,
   `templates/` and `references/` must stay next to `SKILL.md`.
2. Python deps (via uv).
3. `ffmpeg` + `ffprobe`.
4. A transcription engine: whisper.cpp + a model (free, local) and/or an ElevenLabs API key.
5. Node.js 22+ (HyperFrames graphics run through `npx`; yt-dlp also uses Node for YouTube).
6. yt-dlp ≥ 2025.11.12 — or just uv, which can run the latest yt-dlp on demand.

## Contract

- Do everything yourself. Ask the user only for things you cannot do: an API key, approval before
  installing system packages (`brew`, `apt`), approval before large downloads (whisper models), and
  any password prompt — print the exact command and wait. Never type a password.
- Verify with real commands, not file-existence checks.
- Don't transcribe or render anything at install time beyond the checks below.

## Steps

### 1. Clone

```bash
test -d ~/.claude/skills/video-edit || git clone https://github.com/adukhan98/video-edit ~/.claude/skills/video-edit
cd ~/.claude/skills/video-edit && git pull --ff-only
```

Other agents: clone anywhere stable and symlink the whole folder into the agent's skills directory
(`ln -sfn <clone> ~/.codex/skills/video-edit`), or point its system prompt at `<clone>/SKILL.md`.

### 2. Python deps

```bash
command -v uv >/dev/null || echo "uv missing: brew install uv  (or: curl -LsSf https://astral.sh/uv/install.sh | sh)"
cd ~/.claude/skills/video-edit && uv sync
```

Without uv: `pip install -e ~/.claude/skills/video-edit` and run helpers with `python` instead of
`uv run --project …`.

### 3. System tools (ask before installing)

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v whisper-cli >/dev/null || brew install whisper-cpp
command -v node >/dev/null || brew install node            # need 22+: node --version
brew list yt-dlp >/dev/null 2>&1 && brew upgrade yt-dlp    # or leave it to uvx
command -v deno >/dev/null || brew install deno            # optional: yt-dlp's preferred JS runtime

# Debian/Ubuntu
# sudo apt-get install -y ffmpeg nodejs && pip install -U "yt-dlp[default]"
# whisper.cpp: build from https://github.com/ggml-org/whisper.cpp (whisper-cli)
```

ffmpeg must have `libass`, `zscale` and `libvpx`: `ffmpeg -hide_banner -filters | grep -E " (ass|zscale) "`.

### 4. Transcription

**Local (default, free).** `helpers/transcribe.py` looks for `ggml-<model>.bin` in
`~/.cache/video-edit/models`, `~/.cache/hyperframes/whisper/models` (HyperFrames downloads models
there too), `~/.cache/whisper.cpp` and Homebrew's `share/whisper-cpp`. If none has `medium.en`,
ask the user, then:

```bash
mkdir -p ~/.cache/video-edit/models && curl -L -o ~/.cache/video-edit/models/ggml-medium.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin      # ~1.5 GB
```

(`ggml-medium.bin` for non-English footage.)

**ElevenLabs (optional, best quality: verbatim fillers, speakers, audio events).** Check
`$ELEVENLABS_API_KEY` and `~/.claude/skills/video-edit/.env` first. If the user wants it and has
none, ask once for a key from https://elevenlabs.io/app/settings/api-keys, then:

```bash
printf 'ELEVENLABS_API_KEY=%s\n' "$KEY" > ~/.claude/skills/video-edit/.env && chmod 600 ~/.claude/skills/video-edit/.env
curl -s -o /dev/null -w '%{http_code}\n' -H "xi-api-key: $KEY" https://api.elevenlabs.io/v1/user   # 200 = good
```

Never echo the key back, never commit `.env`. With a key present, `transcribe.py` uses Scribe
automatically (`--engine whisper` forces local).

### 5. Verify

```bash
cd ~/.claude/skills/video-edit
uv run --project . python -m unittest discover -s tests -q
uv run --project . helpers/platforms.py
uv run --project . helpers/fetch.py --help >/dev/null && echo "fetch OK"
npx --yes hyperframes --version
whisper-cli --help >/dev/null 2>&1 && echo "whisper.cpp OK"
```

### 6. Hand off

Tell the user, briefly: where the skill lives; to put footage and brand files in a folder and start
Claude Code there; a good first message ("Edit these into a 45-second reel using our brand
guidelines in brand/"); that it will check in once with a brand board and a plan before rendering;
and that everything lands in `<folder>/edit/`.

## Updating

`cd ~/.claude/skills/video-edit && git pull --ff-only && uv sync`
